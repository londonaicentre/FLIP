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

"""Data utilities for the Ark+ evaluation executor.

This file defines label constants, data loading, and transforms used by the
FLIP_EVALUATOR for the DECAF chest X-ray primary evaluation task.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import monai.transforms as mt
import numpy as np
import pandas as pd
import pydicom
import torch
from flip import FLIP
from flip.constants import FlipConstants, ResourceType
from monai.config import KeysCollection
from monai.transforms.transform import MapTransform


def _is_local_dev() -> bool:
    """Whether to read data from local files (simulator) or the FLIP API (real trust).

    ``True`` in the NVFLARE simulator (``LOCAL_DEV=true``): resolve data from local
    ``SITE_DATA``/``DEV_*`` paths. ``False`` on a real federated client: ignore
    ``SITE_DATA`` and fetch the dataframe + DICOMs from the trust APIs via the FLIP
    package. Mirrors the flip package's own ``LOCAL_DEV`` switch
    (``flip.core.factory`` → ``FLIPStandardDev`` vs ``FLIPStandardProd``).
    """
    return bool(FlipConstants.LOCAL_DEV)


# ---------------------------------------------------------------------------
# Custom MONAI transform: 1→3 channel repeat + ImageNet normalization
# ---------------------------------------------------------------------------
class RepeatChannelImageNetNormalized(MapTransform):
    """Repeat 1-channel to 3-channel RGB and apply ImageNet normalization.

    Expects input shape ``(1, H, W)`` in range ``[0, 1]`` (as produced by
    ``ScaleIntensityd``).  Outputs ``(3, H, W)`` with ImageNet-standard
    mean/std per channel.
    """

    def __init__(self, keys: KeysCollection, allow_missing_keys: bool = False):
        super().__init__(keys, allow_missing_keys)
        self.mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
        self.std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)

    def __call__(self, data):
        d = dict(data)
        for key in self.key_iterator(d):
            img = d[key]
            if img.shape[-3] == 1:
                img = img.repeat_interleave(3, dim=-3)
            d[key] = (img - self.mean.to(img.device)) / (self.std.to(img.device))
        return d


# ---------------------------------------------------------------------------
# Label-mapping registry
#
# Each entry bundles:
#   source_labels  — dict of source label name → column index (the fixed
#                    output order of a pre-trained model head).
#   mapping        — rules for extracting each target label from the
#                    source columns:
#                    ("direct", name)  → column of the named source label.
#                    ("max", [names])  → element-wise max of those columns.
#
# Add new entries here to support different pre-trained sources or
# different target-label sets without changing the calling code.
# ---------------------------------------------------------------------------

MAPPING_REGISTRY: dict[str, dict] = {
    "nih14_5class": {
        "source_labels": {
            "Atelectasis": 0,
            "Cardiomegaly": 1,
            "Effusion": 2,
            "Infiltration": 3,
            "Mass": 4,
            "Nodule": 5,
            "Pneumonia": 6,
            "Pneumothorax": 7,
            "Consolidation": 8,
            "Edema": 9,
            "Emphysema": 10,
            "Fibrosis": 11,
            "Pleural_Thickening": 12,
            "Hernia": 13,
        },
        "mapping": {
            "Effusion": ("direct", "Effusion"),
            "Consolidation": ("direct", "Consolidation"),
            "Infiltration": ("direct", "Infiltration"),
            "Lung Nodule or Mass": ("max", ["Mass", "Nodule"]),
            "Pneumothorax": ("direct", "Pneumothorax"),
        },
    },
}


def get_mapping(name: str) -> dict:
    if name not in MAPPING_REGISTRY:
        raise KeyError(f"Unknown label mapping {name!r}. Available: {list(MAPPING_REGISTRY.keys())}")
    return MAPPING_REGISTRY[name]


# ---------------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Lesion helpers (same pattern as the classification tutorial)
# ---------------------------------------------------------------------------
@dataclass
class Lesion:
    id: int
    lesion: str


@dataclass
class SiteDataConfig:
    site_name: str
    images_dir: str | None = None
    dataframe: str | None = None


class LesionDict:
    def __init__(self, items: Sequence[Lesion]):
        self.items = list(items)

    def contains(self, element_value: str) -> bool:
        return any(item.lesion == element_value for item in self.items)

    def get_lesion_list(self) -> list[str]:
        return [item.lesion for item in sorted(self.items, key=lambda x: x.id)]


def get_lesions(config: dict | None = None) -> LesionDict:
    cfg = config or load_config()
    lesions = []
    for key, name in cfg["LESIONS"].items():
        idx = int(key)
        if idx >= 0:
            lesions.append(Lesion(id=idx, lesion=name))
    return LesionDict(sorted(lesions, key=lambda x: x.id))


def get_labels_from_radiology_row(
    radiology_row, lesions: LesionDict, value_to_numerical: dict, normal_label: str
) -> dict[str, int]:
    yes_str = value_to_numerical.get("1", value_to_numerical.get(1, "Yes"))
    no_str = value_to_numerical.get("0", value_to_numerical.get(0, "No"))
    columns = radiology_row.keys()
    override_negative = normal_label in columns and radiology_row[normal_label] == yes_str
    binary = {yes_str: 1, no_str: 0, 1: 1, 0: 0, "1": 1, "0": 0}

    out = {}
    for lesion in lesions.items:
        if override_negative:
            out[lesion.lesion] = 0
        elif lesion.lesion in columns:
            out[lesion.lesion] = binary.get(radiology_row[lesion.lesion], -1)
        else:
            out[lesion.lesion] = -1
    return out


def get_lesion_label(in_batch: dict, lesions: LesionDict) -> np.ndarray:
    """Return a [batch, num_lesions] float label array from a MONAI batch dict."""
    labels = []
    for les in sorted(lesions.items, key=lambda x: x.id):
        labels.append(in_batch[les.lesion])
    return np.stack(labels, axis=1).astype(np.float32)


def normalize_site_name(site_name: str | None) -> str:
    name = (site_name or "").strip()
    if not name:
        return ""
    if name.startswith("site") and "-" not in name and len(name) > 4:
        suffix = name[4:]
        if suffix.isdigit():
            return f"site-{suffix}"
    return name


def get_site_data_config(config: dict | None = None, site_name: str | None = None) -> SiteDataConfig:
    cfg = config or load_config()
    requested_site = normalize_site_name(site_name)
    site_data = cfg.get("SITE_DATA", {})

    entry = None
    for candidate in [requested_site, (site_name or "").strip(), "default"]:
        if candidate and candidate in site_data:
            entry = site_data[candidate]
            break

    if entry is None:
        entry = {}

    images_dir = entry.get("images_dir") or os.environ.get("DEV_IMAGES_DIR")
    dataframe = entry.get("dataframe") or os.environ.get("DEV_DATAFRAME")
    resolved_site = requested_site or "default"
    return SiteDataConfig(site_name=resolved_site, images_dir=images_dir, dataframe=dataframe)


# ---------------------------------------------------------------------------
# Data loading (evaluation-only — load ALL samples, no train/val split)
# ---------------------------------------------------------------------------
def _read_dataframe(dataframe_path: str | None = None) -> pd.DataFrame:
    path = dataframe_path or os.environ.get("DEV_DATAFRAME")
    if path and Path(path).exists():
        return pd.read_csv(path, sep=None, engine="python")
    raise RuntimeError(f"Dataframe path is not set or does not exist: {path!r}")


def _load_dataframe(site_cfg: SiteDataConfig, project_id: str = "", query: str = "") -> pd.DataFrame:
    """Load the cohort dataframe: local CSV in the simulator, FLIP API on a real trust."""
    if _is_local_dev():
        return _read_dataframe(site_cfg.dataframe)
    return FLIP().get_dataframe(project_id, query)


def _find_accession_column(df: pd.DataFrame) -> str:
    for col in ["accession_id", "accession_number", "accession", "AccessionNumber"]:
        if col in df.columns:
            return col
    raise KeyError(f"Could not find accession column in dataframe columns: {list(df.columns)}")


def _dicoms_for_accession(
    accession_id: str,
    project_id: str = "",
    images_dir: str | None = None,
) -> list[Path]:
    if _is_local_dev():
        # Local test-data path used by SITE_DATA / DEV_IMAGES_DIR (simulator only).
        root_path = images_dir or os.environ.get("DEV_IMAGES_DIR")
        if root_path:
            root = Path(root_path)
            candidates = []
            for p in [root / str(accession_id), root / f"{accession_id}"]:
                if p.exists():
                    candidates.extend(p.rglob("*.dcm"))
            if not candidates and root.exists():
                candidates.extend(root.rglob(f"*{accession_id}*.dcm"))
            if not candidates and root.exists():
                all_dicoms = list(root.rglob("*.dcm"))
                if len(all_dicoms) <= 500:
                    candidates = all_dicoms
            return sorted(set(candidates))
        return []

    # Real FLIP client path (LOCAL_DEV=false): fetch DICOMs from the trust imaging-api.
    if project_id:
        flip = FLIP()
        folder = flip.get_by_accession_number(project_id, accession_id, resource_type=[ResourceType.DICOM])
        return sorted(Path(folder).rglob("*.dcm"))

    return []


def build_eval_datalist(
    config: dict | None = None,
    site_name: str | None = None,
    project_id: str | None = None,
    query: str | None = None,
    logger=None,
) -> list[dict]:
    """Load ALL samples for evaluation (no train/val/test split)."""
    cfg = config or load_config()
    site_cfg = get_site_data_config(cfg, site_name)
    lesions = get_lesions(cfg)
    normal_key = cfg.get("LESIONS", {}).get("-1", "Lungs in normal arrangement")
    value_to_numerical = cfg.get("value_to_numerical", {"1": "Yes", "0": "No"})
    project_id = project_id if project_id is not None else os.environ.get("PROJECT_ID", "")
    query = query if query is not None else os.environ.get("QUERY", "")
    df = _load_dataframe(site_cfg, project_id=project_id, query=query)
    accession_col = _find_accession_column(df)

    if logger is not None:
        logger.info(
            "Loading xray data for site=%s images_dir=%s dataframe=%s",
            site_cfg.site_name,
            site_cfg.images_dir,
            site_cfg.dataframe,
        )

    datalist = []
    seen_paths = set()
    skipped_accessions = 0
    for _, row in df.iterrows():
        accession_id = str(row[accession_col])
        labels = get_labels_from_radiology_row(row, lesions, value_to_numerical, normal_label=normal_key)
        try:
            dicom_paths = _dicoms_for_accession(accession_id, project_id=project_id, images_dir=site_cfg.images_dir)
        except Exception as exc:
            # A single accession failing to fetch (e.g. a study whose DICOM resource never made it
            # into the trust imaging backend during the image pull) must not abort the whole
            # cross-site evaluation — mirror the trainer's skip-and-carry-on (FLIP#677). The run
            # evaluates on the accessions that ARE retrievable.
            skipped_accessions += 1
            if logger is not None:
                logger.warning("Skipping accession %s: failed to fetch DICOMs (%s)", accession_id, exc)
            continue
        for img in dicom_paths:
            if img in seen_paths:
                continue
            try:
                _ = pydicom.dcmread(str(img), stop_before_pixels=True)
            except Exception:
                continue
            item: dict = {"image": str(img)}
            item.update(labels)
            datalist.append(item)
            seen_paths.add(img)

    if skipped_accessions and logger is not None:
        logger.warning(
            "Skipped %d accession(s) whose DICOMs could not be fetched; evaluating the remaining %d sample(s).",
            skipped_accessions,
            len(datalist),
        )

    if not datalist:
        raise RuntimeError(
            f"No DICOM image/label pairs found for site={site_cfg.site_name!r}. "
            f"Check images_dir={site_cfg.images_dir!r} "
            f"dataframe={site_cfg.dataframe!r}"
        )

    if logger is not None:
        logger.info("Loaded %d samples for evaluation.", len(datalist))
    return datalist


# ---------------------------------------------------------------------------
# X-ray evaluation transforms
# ---------------------------------------------------------------------------
def _ensure_image_channel_first(image):
    array = np.asarray(image)
    if array.ndim == 2:
        return array[None, ...]
    if array.ndim == 3:
        if array.shape[-1] in (1, 3):
            return np.moveaxis(array, -1, 0)
        if array.shape[0] in (1, 3):
            return array
    return array


def get_xray_transforms(input_size: int = 768):
    transforms = [
        mt.LoadImaged(keys=["image"]),
        mt.Lambdad(keys=["image"], func=_ensure_image_channel_first),
        mt.Resized(keys=["image"], spatial_size=[input_size, input_size]),
        # mt.Rotate90d(keys=["image"], k=-1),
        mt.Flipd(keys=["image"], spatial_axis=1),
        mt.ScaleIntensityd(keys=["image"], channel_wise=True),
        mt.EnsureTyped(keys=["image"]),
        RepeatChannelImageNetNormalized(keys=["image"]),
    ]
    return mt.Compose(transforms)
