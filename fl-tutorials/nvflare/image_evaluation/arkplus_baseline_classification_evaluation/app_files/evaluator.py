"""FLIP_EVALUATOR for the Ark+ DECAF chest X-ray evaluation.

Evaluates a single Ark+ model (zero-shot foundation) on
hold-out data and reports per-lesion AUROC.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from monai.data import DataLoader, Dataset

from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import FLContextKey, ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal

from data_utils import (
    build_eval_datalist,
    get_lesion_label,
    get_mapping,
    get_xray_transforms,
    Lesion,
    LesionDict,
)
from metrics_utils import apply_label_mapping, compute_auroc
from models import model_paths

logger = logging.getLogger(__name__)


class FLIP_EVALUATOR(Executor):
    """Evaluate one or more Ark+ chest X-ray models on hold-out data.
    
    Each model entry in ``config.json`` may specify a different ``INPUT_SIZE``
    and AMP settings.  Every model runs on the same samples (same datalist)
    but with its own transforms and dataloader.
    """

    def __init__(
        self,
        evaluate_task_name="evaluation",
        project_id="",
        query="",
    ):
        super().__init__()

        self._evaluate_task_name = evaluate_task_name
        self._project_id = project_id
        self._query = query

        # -------------------------------------------------------------------
        # 1. Config
        # -------------------------------------------------------------------
        self.config = {}
        working_dir = Path(__file__).parent.resolve()
        config_path = working_dir / "config.json"
        with open(str(config_path)) as f:
            self.config = json.load(f)

        raw_lesions = self.config.get("LESIONS", {})
        self._lesion_items = [
            (int(k), v) for k, v in raw_lesions.items() if int(k) >= 0
        ]

        self._lesion_dict = LesionDict(
            items=[Lesion(id=idx, lesion=name) for idx, name in self._lesion_items]
        )

        self._batch_size = int(self.config.get("BATCH_SIZE", 1))

        # -------------------------------------------------------------------
        # 2. Models — one per config["models"] entry
        # -------------------------------------------------------------------
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.models: dict[str, torch.nn.Module] = {}
        self.model_configs: dict[str, dict] = {}
        self._transforms: dict[str, torch.nn.Module] = {}

        for model_name, model_values in self.config.get("models", {}).items():
            ark_cfg = model_values.get("arkplus_config", {})

            logger.info(
                "Building model %s with NUM_CLASSES_LIST=%s HEAD_ID=%s",
                model_name,
                ark_cfg.get("NUM_CLASSES_LIST"),
                ark_cfg.get("HEAD_ID"),
            )

            model = model_paths[model_values["path"]]
            model.to(self.device)
            model.eval()
            self.models[model_name] = model

            # Per-model configuration
            head_id = int(ark_cfg.get("HEAD_ID", 0))
            num_classes_list = list(ark_cfg.get("NUM_CLASSES_LIST", [5]))
            input_size = int(ark_cfg.get("INPUT_SIZE", 768))
            amp = bool(ark_cfg.get("USE_AMP", False)) and torch.cuda.is_available()
            self.model_configs[model_name] = {
                "head_id": head_id,
                "num_classes_list": num_classes_list,
                "load_backbone_only": bool(ark_cfg.get("LOAD_BACKBONE_ONLY", False)),
                "input_size": input_size,
                "amp": amp,
            }

            # Resolve label mapping for this model (KeyError if missing from config)
            map_name = self.config["label_mapping"][model_name]
            if map_name is not None:
                entry = get_mapping(map_name)
                n_head = num_classes_list[head_id]
                n_source = len(entry["source_labels"])
                if n_head != n_source:
                    raise ValueError(
                        f"{model_name}: head {head_id} outputs {n_head} classes, "
                        f"but mapping {map_name!r} expects {n_source} source labels."
                    )
                self.model_configs[model_name]["source_labels"] = entry["source_labels"]
                self.model_configs[model_name]["mapping"] = entry["mapping"]
            else:
                n_head = num_classes_list[head_id]
                n_target = len(sorted(self._lesion_items, key=lambda x: x[0]))
                if n_head != n_target:
                    raise ValueError(
                        f"{model_name}: head {head_id} outputs {n_head} classes (no mapping), "
                        f"but {n_target} target labels are configured."
                )
                self.model_configs[model_name]["source_labels"] = None
                self.model_configs[model_name]["mapping"] = None

            # Per-model transforms (dataloader built lazily in execute)
            self._transforms[model_name] = get_xray_transforms(
                input_size=input_size
            )

        # -------------------------------------------------------------------
        # 3. Data (loaded lazily per-site)
        # -------------------------------------------------------------------
        self._datalist = []
        self._dataloaders: dict[str, DataLoader] = {}
        self._data_loaded = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _autocast(self, enabled: bool = False):
        if hasattr(torch, "amp"):
            return torch.amp.autocast(device_type="cuda", enabled=enabled)
        return torch.cuda.amp.autocast(enabled=enabled)

    def _site_name_from_context(self, fl_ctx: FLContext) -> str:
        from data_utils import normalize_site_name

        site_name = fl_ctx.get_prop(FLContextKey.CLIENT_NAME, "")
        if not site_name:
            try:
                site_name = fl_ctx.get_identity_name()
            except Exception:
                site_name = ""
        return normalize_site_name(site_name) or "default"

    def _ensure_data_loaded(self, site_name: str = "default"):
        if self._data_loaded:
            return

        self._datalist = build_eval_datalist(
            config=self.config,
            site_name=site_name,
            logger=logger,
        )

        # Store accessions and paths once (shared across all models)
        self._accessions: list[str] = [
            Path(item["image"]).parent.name for item in self._datalist
        ]
        self._dicom_paths: list[str] = [
            str(item["image"]) for item in self._datalist
        ]

        # Per-model dataloader (each model may have a different INPUT_SIZE)
        self._dataloaders = {}
        for model_name in self.models:
            ds = Dataset(self._datalist, transform=self._transforms[model_name])
            self._dataloaders[model_name] = DataLoader(
                ds, batch_size=self._batch_size, shuffle=False
            )

        self._data_loaded = True
        logger.info(
            "Evaluation data loaded: %d samples, %d models.",
            len(self._datalist),
            len(self._dataloaders),
        )

    def _load_model_weights_from_dxo(self, model_name: str, dxo_model: DXO):
        """Load weights from a server-provided model DXO into the local model."""
        model = self.models[model_name]

        weights = {
            k: torch.as_tensor(v, device="cpu") for k, v in dxo_model.data.items()
        }
        model.load_state_dict(weights, strict=True)
        model.to(self.device)
        model.eval()

    def _predict_model(
        self, model_name: str, images: torch.Tensor
    ) -> np.ndarray:
        """Run a single model on a batch and return sigmoid probabilities.

        Returns:
            [batch_size, num_target_labels] float32 array of sigmoid outputs
            aligned to the configured target label order.
        """
        model = self.models[model_name]
        cfg = self.model_configs[model_name]
        head_id = cfg["head_id"]

        with torch.no_grad():
            with self._autocast(enabled=cfg["amp"]):
                _features, logits = model(images, head_n=head_id)
                probs = torch.sigmoid(logits).cpu().numpy()

        # Map model output to target labels (no mapping = output is already aligned)
        if cfg["mapping"] is None:
            return probs
        mapped = apply_label_mapping(
            probs,
            source_labels=cfg["source_labels"],
            mapping=cfg["mapping"],
            decaf_labels=[name for _, name in sorted(self._lesion_items, key=lambda x: x[0])],
        )
        return np.column_stack([mapped[name] for _, name in sorted(self._lesion_items, key=lambda x: x[0])])

    def _compute_metrics(
        self,
        all_targets: dict[str, np.ndarray],
        all_predictions: dict[str, np.ndarray],
        accessions: list[str],
        paths: list[str],
        output_dir: Path,
    ) -> dict:
        """Compute per-model AUROC and DeLong pairwise tests, save CSVs.

        Returns a DXO-compatible metrics dict:
            {model_name: {auroc_<lesion>: ..., ...}}
        """
        metrics_dxo: dict[str, dict[str, float]] = {}
        model_names = list(self.models.keys())
        n_models = len(model_names)

        # --- Per-model AUROC ---
        for model_name in model_names:
            preds = all_predictions[model_name]
            targets = all_targets[model_name]
            model_metrics: dict[str, float] = {}
            for idx, name in sorted(self._lesion_items, key=lambda x: x[0]):
                y_true = targets[:, idx]
                y_score = preds[:, idx]
                auroc = compute_auroc(y_true, y_score)
                model_metrics[f"auroc_{name}"] = auroc
            metrics_dxo[model_name] = model_metrics

        # --- Save per-model predictions CSV ---
        for model_name in model_names:
            preds = all_predictions[model_name]
            targets = all_targets[model_name]
            rows = {"accession_id": accessions, "dicom_path": paths}
            for idx, name in sorted(self._lesion_items, key=lambda x: x[0]):
                rows[f"target_{name}"] = targets[:, idx].astype(int)
                rows[f"pred_{name}"] = preds[:, idx]
            pdf = pd.DataFrame(rows)
            pdf.to_csv(output_dir / f"{model_name}_predictions.csv", index=False)

        # --- Save per-model metrics CSV ---
        metrics_rows = []
        for model_name in model_names:
            for _, name in sorted(self._lesion_items, key=lambda x: x[0]):
                auroc = metrics_dxo[model_name][f"auroc_{name}"]
                metrics_rows.append(
                    {
                        "model": model_name,
                        "label": name,
                        "auroc": auroc,
                    }
                )
        pd.DataFrame(metrics_rows).to_csv(
            output_dir / "per_model_metrics.csv", index=False
        )

        # --- Save metadata ---
        metadata = {
            "primary_eval_note": self.config.get("primary_eval_note", ""),
            "num_samples": len(accessions),
            "models": list(model_names),
            "decaf_labels": [name for _, name in sorted(self._lesion_items, key=lambda x: x[0])],
            "output_files": [
                "per_model_metrics.csv",
                *(f"{m}_predictions.csv" for m in model_names),
            ],
        }
        with (output_dir / "run_metadata.json").open("w") as f:
            json.dump(metadata, f, indent=2)

        return metrics_dxo

    # ------------------------------------------------------------------
    # NVFLARE entry point
    # ------------------------------------------------------------------
    def execute(
        self,
        task_name: str,
        shareable: Shareable,
        fl_ctx: FLContext,
        abort_signal: Signal,
    ) -> Shareable:
        logger.info(
            "Received task: %s (identity=%s)",
            task_name,
            fl_ctx.get_identity_name(),
        )

        if task_name != self._evaluate_task_name:
            return make_reply(ReturnCode.TASK_UNKNOWN)

        site_name = self._site_name_from_context(fl_ctx)
        self._ensure_data_loaded(site_name=site_name)

        # -------------------------------------------------------------------
        # Parse the incoming shareable — the DXO is a COLLECTION of
        # per-model weight DXOs sent by EvaluationPTModelLocator.
        # -------------------------------------------------------------------
        dxo = from_shareable(shareable)

        for model_name in self.models:
            self.log_info(
                fl_ctx,
                f"Loading model {model_name} at client {fl_ctx.get_identity_name()}...",
            )
            dxo_model = dxo.data.get(model_name)
            if not dxo_model.data_kind == DataKind.WEIGHTS:
                self.log_exception(
                    fl_ctx,
                    f"DXO for model {model_name} is of type "
                    f"{dxo_model.data_kind} but expected type WEIGHTS.",
                )
                return make_reply(ReturnCode.BAD_TASK_DATA)
            self._load_model_weights_from_dxo(model_name, dxo_model)
            self.log_info(
                fl_ctx,
                f"Model {model_name} weights loaded at client "
                f"{fl_ctx.get_identity_name()}.",
            )

        if abort_signal.triggered:
            return make_reply(ReturnCode.TASK_ABORTED)

        # -------------------------------------------------------------------
        # Run inference per model — each model uses its own dataloader
        # (with its own INPUT_SIZE transform) but the same underlying datalist.
        # -------------------------------------------------------------------
        all_predictions: dict[str, list[np.ndarray]] = {
            m: [] for m in self.models
        }
        all_targets: dict[str, list[np.ndarray]] = {
            m: [] for m in self.models
        }

        for model_name in self.models:
            dataloader = self._dataloaders[model_name]
            logger.info(
                "Running inference for model %s (%d batches, INPUT_SIZE=%d, AMP=%s)",
                model_name,
                len(dataloader),
                self.model_configs[model_name].get("input_size", 768),
                self.model_configs[model_name]["amp"],
            )

            for batch_idx, batch in enumerate(dataloader):
                if abort_signal.triggered:
                    return make_reply(ReturnCode.TASK_ABORTED)

                images = batch["image"].to(self.device).float()
                labels = get_lesion_label(batch, self._lesion_dict)

                decaf_probs = self._predict_model(model_name, images)
                all_predictions[model_name].append(decaf_probs)
                all_targets[model_name].append(labels)

                if (batch_idx + 1) % 10 == 0:
                    logger.info(
                        "Model %s batch %d / %d",
                        model_name,
                        batch_idx + 1,
                        len(dataloader),
                    )

        # Concatenate batch results
        for model_name in self.models:
            all_predictions[model_name] = np.concatenate(
                all_predictions[model_name], axis=0
            )
            all_targets[model_name] = np.concatenate(
                all_targets[model_name], axis=0
            )

        logger.info(
            "Inference complete: %d samples across %d models.",
            len(self._dicom_paths),
            len(self.models),
        )

        # -------------------------------------------------------------------
        # Compute metrics and save outputs
        # -------------------------------------------------------------------
        engine = fl_ctx.get_engine()
        workspace = engine.get_workspace()
        run_dir = Path(workspace.get_run_dir(fl_ctx.get_job_id()))
        output_dir = run_dir / "manual_save_eval_results"
        output_dir.mkdir(parents=True, exist_ok=True)

        metrics_dxo = self._compute_metrics(
            all_targets=all_targets,
            all_predictions=all_predictions,
            accessions=self._accessions,
            paths=self._dicom_paths,
            output_dir=output_dir,
        )

        dxo = DXO(data_kind=DataKind.METRICS, data=metrics_dxo)
        return dxo.to_shareable()
