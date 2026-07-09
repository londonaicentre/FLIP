# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Client-API evaluator for the Ark+ multimodel chest X-ray evaluation tutorial.

This is the NVFLARE Client-API counterpart of the legacy multimodel ``FLIP_EVALUATOR(Executor)``. Where
the single-model baseline evaluator scores one server-broadcast checkpoint, this tutorial reports a
**pairwise** comparison (per-lesion AUROC for every model plus DeLong significance tests between them),
which needs every model's per-sample scores on the *same* cohort at once. The stock Client-API validate
path broadcasts one model per ``validate`` task, so it can't supply both models together.

Design: the server's ``EvaluationModelLocator`` loads every checkpoint in ``config.json['models']``
(from the app's ``custom/`` in the simulator, from the FL API's staging volume in production) and
``GlobalModelEval`` broadcasts each one as its own ``validate`` task. This evaluator scores the
**broadcast** weights (``input_model.params``) — clients never read the ``.pt`` files, so the same code
path runs on the local simulator (``make run``) and the production FL platform, where the FL API
de-bundles the checkpoints and they never reach the clients.

Two multimodel-specific mechanics on top of the single-model baseline evaluator:

* **Model identity from the broadcast meta.** The stock ``MODEL_OWNER`` header does not survive the
  Client-API ``FLModel`` conversion, so FLIP's ``EvaluationModelLocator`` stamps each broadcast's DXO
  meta with its ``config.json['models']`` entry name (``FlipMetaKey.EVAL_MODEL_NAME``). This evaluator
  reads that key to attribute the received weights; a missing key means the FL server's ``flip`` package
  predates named evaluation broadcasts, and the task fails with that remedy rather than guessing.
* **Cumulative replies.** Pairwise DeLong needs every model's per-sample scores on the same cohort, but
  each ``validate`` task delivers one model. Scores are cached across tasks (the Client-API executor
  keeps this script alive for the whole job), and every reply returns the metrics for **all** models
  scored so far. The server's ``EvaluationJsonGenerator`` keeps one dict per client (last reply wins),
  and a client executes its tasks sequentially — so the final reply, which carries every model's AUROC
  plus the DeLong map, is what lands in ``evaluation_results.json`` (the same shape the legacy executor
  produced).

Only aggregate (cohort-level) metrics are returned — per-lesion AUROC per model and DeLong p-values.
Per-sample (row-level) predictions are deliberately never produced or exported: a per-patient list would
leak the exact evaluation cohort size and be linkable to individual patients (matching the baseline
evaluator's privacy contract; the legacy executor implementation wrote per-sample CSVs, this one omits
them).
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import nvflare.client as flare
import torch
from data_utils import (
    Lesion,
    LesionDict,
    build_eval_datalist,
    get_lesion_label,
    get_mapping,
    get_xray_transforms,
)
from metrics_utils import apply_label_mapping, compute_auroc, delong_roc_test
from models import _build_arkplus_raw
from monai.data import DataLoader, Dataset

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project_id", type=str, default="")
    return parser.parse_args()


def load_query() -> str:
    """Read the cohort query from the client app config (top-level ``query`` key).

    NVFlare's TaskScriptRunner whitespace-splits ``task_script_args``, so the SQL query (which can contain
    spaces) is plumbed via ``config/config_fed_client.json`` rather than as a CLI flag. In dev/simulator
    mode this is ignored — data comes from the local SITE/DEV paths.
    """
    client_cfg = Path(__file__).parent.parent / "config" / "config_fed_client.json"
    if client_cfg.exists():
        try:
            return json.loads(client_cfg.read_text()).get("query", "")
        except Exception:
            return ""
    return ""


def load_config() -> dict:
    """Load the user-supplied config.json that sits next to this script."""
    config_path = Path(__file__).parent.resolve() / "config.json"
    with open(config_path) as f:
        return json.load(f)


def _autocast(enabled: bool = False):
    """AMP autocast context — mirrors the legacy evaluator so inference results are identical."""
    if hasattr(torch, "amp"):
        return torch.amp.autocast(device_type="cuda", enabled=enabled)
    return torch.cuda.amp.autocast(enabled=enabled)


def resolve_models_config(config: dict) -> tuple[list[dict], list[tuple[int, str]], LesionDict]:
    """Resolve every model's evaluation config and the target lesion order from ``config.json``.

    Returns ``(model_specs, lesion_items, lesion_dict)`` where ``model_specs`` is one ordered dict per
    ``config['models']`` entry carrying ``name``, ``checkpoint``, ``arkplus_config``, ``head_id``,
    ``input_size``, ``amp``, ``source_labels`` and ``mapping`` — built exactly as the legacy evaluator did
    (including the head-size-vs-label-mapping validation), so downstream inference is identical.
    """
    raw_lesions = config.get("LESIONS", {})
    lesion_items = [(int(k), v) for k, v in raw_lesions.items() if int(k) >= 0]
    lesion_dict = LesionDict(items=[Lesion(id=idx, lesion=name) for idx, name in lesion_items])

    models_cfg = config.get("models", {})
    if not models_cfg:
        raise ValueError("config.json['models'] is empty; multimodel evaluation expects at least one model.")

    model_specs: list[dict] = []
    for model_name, model_values in models_cfg.items():
        ark_cfg = model_values.get("arkplus_config", {})
        head_id = int(ark_cfg.get("HEAD_ID", 0))
        num_classes_list = list(ark_cfg.get("NUM_CLASSES_LIST", [5]))
        input_size = int(ark_cfg.get("INPUT_SIZE", 768))
        amp = bool(ark_cfg.get("USE_AMP", False)) and torch.cuda.is_available()

        spec: dict = {
            "name": model_name,
            "checkpoint": model_values["checkpoint"],
            "arkplus_config": ark_cfg,
            "head_id": head_id,
            "input_size": input_size,
            "amp": amp,
        }

        map_name = config["label_mapping"][model_name]
        if map_name is not None:
            entry = get_mapping(map_name)
            n_head = num_classes_list[head_id]
            n_source = len(entry["source_labels"])
            if n_head != n_source:
                raise ValueError(
                    f"{model_name}: head {head_id} outputs {n_head} classes, "
                    f"but mapping {map_name!r} expects {n_source} source labels."
                )
            spec["source_labels"] = entry["source_labels"]
            spec["mapping"] = entry["mapping"]
        else:
            n_head = num_classes_list[head_id]
            n_target = len(lesion_items)
            if n_head != n_target:
                raise ValueError(
                    f"{model_name}: head {head_id} outputs {n_head} classes (no mapping), "
                    f"but {n_target} target labels are configured."
                )
            spec["source_labels"] = None
            spec["mapping"] = None

        model_specs.append(spec)

    return model_specs, lesion_items, lesion_dict


# Meta key the server-side EvaluationModelLocator stamps on every evaluation broadcast
# (flip.constants.FlipMetaKey.EVAL_MODEL_NAME). Kept as a literal so this app never imports flip
# symbols that may not exist on older client images — the value is produced on the fl-server.
EVAL_MODEL_NAME_META_KEY = "flip_eval_model_name"


def received_model_name(input_model: flare.FLModel, model_names: list[str]) -> str:
    """Resolve which configured model a ``validate`` broadcast carries, from its DXO meta.

    The server-side ``EvaluationModelLocator`` names every broadcast in the DXO meta. A missing key
    means the FL server's ``flip`` package predates named evaluation broadcasts — fail with that
    remedy rather than guessing which weights arrived.
    """
    name = (input_model.meta or {}).get(EVAL_MODEL_NAME_META_KEY)
    if not name:
        raise ValueError(
            f"Broadcast weights carry no {EVAL_MODEL_NAME_META_KEY!r} meta. The FL server's flip package "
            "predates named evaluation broadcasts (EvaluationModelLocator) — update/redeploy the "
            "fl-server before running the multimodel evaluation."
        )
    if name not in model_names:
        raise ValueError(f"Server broadcast model {name!r} is not configured; expected one of {model_names}.")
    return name


def build_model_from_params(spec: dict, params: dict, device: torch.device) -> torch.nn.Module:
    """Build *spec*'s architecture and load the server-broadcast weights onto *device*.

    The locator normalises every checkpoint to a bare state dict before broadcasting, so ``params`` is
    applied directly with ``strict=True`` — any architecture/weight mismatch fails loudly.
    """
    model = _build_arkplus_raw(spec["arkplus_config"])
    model.load_state_dict({k: torch.as_tensor(v) for k, v in params.items()}, strict=True)
    model.to(device)
    model.eval()
    return model


def predict(
    model: torch.nn.Module,
    images: torch.Tensor,
    spec: dict,
    lesion_items: list[tuple[int, str]],
) -> np.ndarray:
    """Run a model on a batch and return sigmoid probabilities aligned to the target lesion order.

    Mirrors the legacy ``_predict_model`` exactly (same head, AMP autocast, and label mapping).
    """
    head_id = spec["head_id"]
    with torch.no_grad():
        with _autocast(enabled=spec["amp"]):
            _features, logits = model(images, head_n=head_id)
            probs = torch.sigmoid(logits).cpu().numpy()

    if spec["mapping"] is None:
        return probs
    decaf_labels = [name for _, name in sorted(lesion_items, key=lambda x: x[0])]
    mapped = apply_label_mapping(
        probs,
        source_labels=spec["source_labels"],
        mapping=spec["mapping"],
        decaf_labels=decaf_labels,
    )
    return np.column_stack([mapped[name] for name in decaf_labels])


def evaluate_model(
    model: torch.nn.Module,
    loader: DataLoader,
    device: torch.device,
    spec: dict,
    lesion_items: list[tuple[int, str]],
    lesion_dict: LesionDict,
) -> tuple[np.ndarray, np.ndarray]:
    """Score one model over the local cohort and return ``(predictions, targets)`` arrays.

    Both arrays are ``[num_samples, num_lesions]``, column-ordered by sorted lesion id, so per-lesion
    slices align across models for the DeLong comparison.
    """
    model.eval()
    all_predictions: list[np.ndarray] = []
    all_targets: list[np.ndarray] = []

    for batch_idx, batch in enumerate(loader):
        images = batch["image"].to(device).float()
        labels = get_lesion_label(batch, lesion_dict)
        all_predictions.append(predict(model, images, spec, lesion_items))
        all_targets.append(labels)
        if (batch_idx + 1) % 10 == 0:
            logger.info("[%s] evaluated %d / %d batches", spec["name"], batch_idx + 1, len(loader))

    preds = np.concatenate(all_predictions, axis=0)
    targets = np.concatenate(all_targets, axis=0)
    return preds, targets


def compute_metrics(
    model_names: list[str],
    all_predictions: dict[str, np.ndarray],
    all_targets: dict[str, np.ndarray],
    lesion_items: list[tuple[int, str]],
) -> dict:
    """Compute per-model per-lesion AUROC and (for >= 2 models) pairwise DeLong p-values.

    Returns the aggregate-only metrics dict the server collects into ``evaluation_results.json``:
    ``{model_name: {auroc_<lesion>: ..., delong_p_values: {lesion: {other_model: p}}}}``. The DeLong
    ``delong_p_values`` sub-dict matches the legacy executor's shape — diagonal (model vs. self) is
    hardcoded ``1.0``; off-diagonal is the two-sided DeLong test. No per-sample data is included.
    """
    sorted_lesions = sorted(lesion_items, key=lambda x: x[0])
    n_models = len(model_names)

    metrics: dict[str, dict] = {}
    for model_name in model_names:
        preds = all_predictions[model_name]
        targets = all_targets[model_name]
        model_metrics: dict = {}
        for idx, name in sorted_lesions:
            model_metrics[f"auroc_{name}"] = float(compute_auroc(targets[:, idx], preds[:, idx]))
        metrics[model_name] = model_metrics

    if n_models < 2:
        return metrics

    # Pairwise DeLong test between every model pair, per lesion. Diagonal (self vs self) is 1.0.
    delong_map: dict[str, dict[str, dict[str, float]]] = {
        m: {name: {other: 1.0 for other in model_names} for _, name in sorted_lesions} for m in model_names
    }
    for i in range(n_models):
        for j in range(i + 1, n_models):
            name_a, name_b = model_names[i], model_names[j]
            for idx, name in sorted_lesions:
                result = delong_roc_test(
                    all_targets[name_a][:, idx],
                    all_predictions[name_a][:, idx],
                    all_predictions[name_b][:, idx],
                )
                p = float(result["pvalue"])
                delong_map[name_a][name][name_b] = p
                delong_map[name_b][name][name_a] = p

    for model_name in model_names:
        metrics[model_name]["delong_p_values"] = delong_map[model_name]

    return metrics


def main() -> None:
    args = parse_args()
    config = load_config()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    model_specs, lesion_items, lesion_dict = resolve_models_config(config)
    model_names = [spec["name"] for spec in model_specs]
    specs_by_name = {spec["name"]: spec for spec in model_specs}

    # flare.init() must precede any other flare.* call (e.g. get_site_name()), which is why data loading
    # happens after init here — this evaluator needs the site name to select the per-site cohort.
    flare.init()

    # Per-site cohort in the simulator (site-1/site-2 score distinct hold-out sets); ignored in production,
    # where flip.get_dataframe / get_by_accession_number fetch from the trust APIs.
    site_name = flare.get_site_name()
    datalist = build_eval_datalist(
        config=config,
        site_name=site_name,
        project_id=args.project_id,
        query=load_query(),
        logger=logger,
    )
    batch_size = int(config.get("BATCH_SIZE", 1))
    # One dataloader per model — each model may declare a different INPUT_SIZE (its own transforms) but
    # they share the same underlying datalist, so per-lesion predictions stay row-aligned across models.
    loaders = {
        spec["name"]: DataLoader(
            Dataset(datalist, transform=get_xray_transforms(input_size=spec["input_size"])),
            batch_size=batch_size,
            shuffle=False,
        )
        for spec in model_specs
    }

    # Per-sample scores cached across validate tasks: each task carries one model's weights, but the
    # DeLong comparison needs every model's scores on the same cohort.
    all_predictions: dict[str, np.ndarray] = {}
    all_targets: dict[str, np.ndarray] = {}

    while flare.is_running():
        input_model = flare.receive()
        if input_model is None:
            break

        if not flare.is_evaluate():
            logger.warning("Received a non-evaluation task; ignoring.")
            continue

        name = received_model_name(input_model, model_names)
        if name in all_predictions:
            logger.info("Model %s already scored; replying with cached metrics.", name)
        else:
            model = build_model_from_params(specs_by_name[name], input_model.params, device)
            preds, targets = evaluate_model(
                model, loaders[name], device, specs_by_name[name], lesion_items, lesion_dict
            )
            # Only the scores are kept — the model is released before the next broadcast so a single
            # model's footprint bounds GPU memory, whatever len(config['models']) is.
            del model
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            all_predictions[name] = preds
            all_targets[name] = targets
            logger.info("Scored model %s (%d/%d models done).", name, len(all_predictions), len(model_names))

        # Reply with every model scored so far. The server keeps one metrics dict per client (last
        # reply wins) and a client executes its tasks sequentially, so the reply to the final validate
        # task — every model's AUROC plus the pairwise DeLong map — is what lands in
        # evaluation_results.json.
        scored_names = [n for n in model_names if n in all_predictions]
        flare.send(flare.FLModel(metrics=compute_metrics(scored_names, all_predictions, all_targets, lesion_items)))


if __name__ == "__main__":
    main()
