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

"""Client-API evaluator for the Ark+ baseline chest X-ray evaluation tutorial.

This is the NVFLARE Client-API counterpart of the legacy ``FLIP_EVALUATOR(Executor)``. The server
(``CrossSiteModelEval`` + ``EvaluationModelLocator``, wired by ``FlipEvalRecipe``) loads the single
uploaded Ark+ checkpoint and broadcasts it to every client as one ``FLModel``; this script implements the
canonical ``is_evaluate()`` loop — receive the global model, score it on the local cohort, and send back
**aggregate-only** per-lesion AUROC. There is no ``Executor``/``Shareable`` plumbing and no multi-model
``COLLECTION`` unwrapping; the weights arrive as ``input_model.params``.

Only cohort-level per-lesion AUROC is returned (``{model_name: {auroc_<lesion>: ...}}``). Per-sample
(row-level) predictions are deliberately never produced or exported: a per-patient list would leak the
exact evaluation cohort size and be linkable to individual patients.

The numerical pipeline (model, transforms, head inference, label mapping, AUROC) is identical to the
legacy tutorial's, so the reported metrics match — only the FL transport differs.
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
from metrics_utils import apply_label_mapping, compute_auroc
from models import get_model
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


def resolve_model_config(config: dict) -> tuple[str, dict, list[tuple[int, str]], LesionDict]:
    """Resolve the single model's evaluation config and the target lesion order from ``config.json``.

    Returns ``(model_name, model_cfg, lesion_items, lesion_dict)`` where ``model_cfg`` carries ``head_id``,
    ``input_size``, ``amp``, ``source_labels`` and ``mapping`` — built exactly as the legacy evaluator did
    (including the head-size-vs-label-mapping validation), so downstream inference is identical.
    """
    raw_lesions = config.get("LESIONS", {})
    lesion_items = [(int(k), v) for k, v in raw_lesions.items() if int(k) >= 0]
    lesion_dict = LesionDict(items=[Lesion(id=idx, lesion=name) for idx, name in lesion_items])

    models_cfg = config.get("models", {})
    if len(models_cfg) != 1:
        raise ValueError(f"Baseline evaluation expects exactly one model; got {len(models_cfg)}.")
    ((model_name, model_values),) = models_cfg.items()
    ark_cfg = model_values.get("arkplus_config", {})

    head_id = int(ark_cfg.get("HEAD_ID", 0))
    num_classes_list = list(ark_cfg.get("NUM_CLASSES_LIST", [5]))
    input_size = int(ark_cfg.get("INPUT_SIZE", 768))
    amp = bool(ark_cfg.get("USE_AMP", False)) and torch.cuda.is_available()

    model_cfg: dict = {"head_id": head_id, "input_size": input_size, "amp": amp}

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
        model_cfg["source_labels"] = entry["source_labels"]
        model_cfg["mapping"] = entry["mapping"]
    else:
        n_head = num_classes_list[head_id]
        n_target = len(lesion_items)
        if n_head != n_target:
            raise ValueError(
                f"{model_name}: head {head_id} outputs {n_head} classes (no mapping), "
                f"but {n_target} target labels are configured."
            )
        model_cfg["source_labels"] = None
        model_cfg["mapping"] = None

    return model_name, model_cfg, lesion_items, lesion_dict


def predict(
    model: torch.nn.Module,
    images: torch.Tensor,
    model_cfg: dict,
    lesion_items: list[tuple[int, str]],
) -> np.ndarray:
    """Run the model on a batch and return sigmoid probabilities aligned to the target lesion order.

    Mirrors the legacy ``_predict_model`` exactly (same head, AMP autocast, and label mapping).
    """
    head_id = model_cfg["head_id"]
    with torch.no_grad():
        with _autocast(enabled=model_cfg["amp"]):
            _features, logits = model(images, head_n=head_id)
            probs = torch.sigmoid(logits).cpu().numpy()

    if model_cfg["mapping"] is None:
        return probs
    decaf_labels = [name for _, name in sorted(lesion_items, key=lambda x: x[0])]
    mapped = apply_label_mapping(
        probs,
        source_labels=model_cfg["source_labels"],
        mapping=model_cfg["mapping"],
        decaf_labels=decaf_labels,
    )
    return np.column_stack([mapped[name] for name in decaf_labels])


def evaluate(
    model: torch.nn.Module,
    loader: DataLoader,
    device: torch.device,
    model_cfg: dict,
    lesion_items: list[tuple[int, str]],
    lesion_dict: LesionDict,
) -> dict:
    """Score the model over the local cohort and return aggregate per-lesion AUROC only.

    Predictions and targets are both column-ordered by sorted lesion id (contiguous 0..N-1), matching the
    legacy evaluator's indexing, so ``auroc_<lesion>`` values are identical. No per-sample data is kept.
    """
    model.eval()
    all_predictions: list[np.ndarray] = []
    all_targets: list[np.ndarray] = []

    for batch_idx, batch in enumerate(loader):
        images = batch["image"].to(device).float()
        labels = get_lesion_label(batch, lesion_dict)
        all_predictions.append(predict(model, images, model_cfg, lesion_items))
        all_targets.append(labels)
        if (batch_idx + 1) % 10 == 0:
            logger.info("Evaluated %d / %d batches", batch_idx + 1, len(loader))

    preds = np.concatenate(all_predictions, axis=0)
    targets = np.concatenate(all_targets, axis=0)

    metrics: dict[str, float] = {}
    for idx, name in sorted(lesion_items, key=lambda x: x[0]):
        metrics[f"auroc_{name}"] = float(compute_auroc(targets[:, idx], preds[:, idx]))
    logger.info("Evaluation finished: " + ", ".join(f"{k}={v:.4f}" for k, v in metrics.items()))
    return metrics


def load_global_weights(model: torch.nn.Module, input_model: flare.FLModel) -> None:
    """Load the server-broadcast weights (sent over the validate task) onto the local model."""
    torch_weights = {k: torch.as_tensor(v) for k, v in input_model.params.items()}
    model.load_state_dict(torch_weights, strict=True)


def main() -> None:
    args = parse_args()
    config = load_config()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("Evaluating on device: %s", device)
    if device.type == "cpu":
        logger.warning(
            "CUDA is unavailable — falling back to CPU inference (much slower). "
            "Check the host GPU/driver state if this is unexpected."
        )

    model_name, model_cfg, lesion_items, lesion_dict = resolve_model_config(config)
    model = get_model().to(device)

    # flare.init() must precede any other flare.* call (e.g. get_site_name()), which is why data loading
    # happens after init here — unlike the spleen reference, this evaluator needs the site name to select
    # the per-site cohort.
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
    loader = DataLoader(
        Dataset(datalist, transform=get_xray_transforms(input_size=model_cfg["input_size"])),
        batch_size=batch_size,
        shuffle=False,
    )

    while flare.is_running():
        input_model = flare.receive()
        if input_model is None:
            break

        if flare.is_evaluate():
            load_global_weights(model, input_model)
            metrics = evaluate(model, loader, device, model_cfg, lesion_items, lesion_dict)
            # Nest under the model name to preserve the legacy output contract:
            # evaluation_results.json = {site: {model_name: {auroc_<lesion>: ...}}}.
            flare.send(flare.FLModel(metrics={model_name: metrics}))
        else:
            logger.warning("Received a non-evaluation task; ignoring.")


if __name__ == "__main__":
    main()
