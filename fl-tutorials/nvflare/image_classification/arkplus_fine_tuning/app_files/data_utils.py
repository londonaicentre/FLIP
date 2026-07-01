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

"""Data utilities for the NVFLARE-style FLIP x-ray tutorial.

This file intentionally keeps the original tutorial data style: a dataframe/CSV
with accession ids and label columns plus DICOM resources in the FLIP test-data
layout. It prepares MONAI datasets whose batch dictionaries contain ``image`` and
one scalar label per lesion.
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
from monai.config import KeysCollection
# from monai.data import DataLoader, Dataset
from monai.transforms.transform import MapTransform

try:
    from flip import FLIP
    from flip.constants import ResourceType
except Exception:  # pragma: no cover - lets local syntax checks work without FLIP installed
    FLIP = None
    ResourceType = None


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


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"


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


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_lesions(config: dict | None = None) -> LesionDict:
    cfg = config or load_config()
    lesions = []
    for key, name in cfg["LESIONS"].items():
        idx = int(key)
        if idx >= 0:
            lesions.append(Lesion(id=idx, lesion=name))
    return LesionDict(sorted(lesions, key=lambda x: x.id))


def get_normal_key(config: dict | None = None) -> str:
    cfg = config or load_config()
    for key, name in cfg.get("LESIONS", {}).items():
        if int(key) < 0:
            return name
    return "Lungs in normal arrangement"


def get_labels_from_radiology_row(radiology_row, lesions: LesionDict, value_to_numerical: dict, normal_label: str):
    # JSON gives string keys; support both str and int access.
    yes_str = value_to_numerical.get("1", value_to_numerical.get(1, "Yes"))
    no_str = value_to_numerical.get("0", value_to_numerical.get(0, "No"))
    columns = radiology_row.keys()
    override_negative = normal_label in columns and radiology_row[normal_label] == yes_str
    binary = {yes_str: 1, no_str: 0, 1: 1, 0: 0, "1": 1, "0": 0}

    out_dict = {}
    for lesion in lesions.items:
        if override_negative:
            out_dict[lesion.lesion] = 0
        elif lesion.lesion in columns:
            out_dict[lesion.lesion] = binary.get(radiology_row[lesion.lesion], -1)
        else:
            out_dict[lesion.lesion] = -1
    return out_dict


def get_lesion_label(in_batch: dict, lesions: LesionDict) -> torch.Tensor:
    out_tensor = [in_batch[les.lesion] for les in sorted(lesions.items, key=lambda x: x.id)]
    return torch.stack(out_tensor, dim=1).float()


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


def get_xray_transforms(is_validation: bool = False):
    cfg = load_config()
    input_size = int(cfg.get("ARKPLUS", {}).get("INPUT_SIZE", 224))
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
    if not is_validation:
        transforms.append(mt.RandAffined(keys=["image"], rotate_range=[-0.05, 0.05], scale_range=[0.01, 0.05]))
    return mt.Compose(transforms)


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


def _read_dataframe(dataframe_path: str | None = None) -> pd.DataFrame:
    path = dataframe_path or os.environ.get("DEV_DATAFRAME")
    if path and Path(path).exists():
        return pd.read_csv(path, sep=None, engine="python")
    raise RuntimeError(f"Dataframe path is not set or does not exist: {path!r}")


def _find_accession_column(df: pd.DataFrame) -> str:
    for col in ["accession_id", "accession_number", "accession", "AccessionNumber"]:
        if col in df.columns:
            return col
    raise KeyError(f"Could not find accession column in dataframe columns: {list(df.columns)}")


def _label_aware_split(datalist, label_names, val_split: float, seed: int, logger=None):
    """
    Guarantee at least one positive sample per label in train and val splits,
    then fill remaining capacity with unassigned items.

    Processing labels in order of rarity (fewest positives first) ensures rare
    labels reserve their slot before common labels consume all split capacity.
    """
    datalist = list(datalist)
    rng = np.random.default_rng(seed)
    rng.shuffle(datalist)

    n_total = len(datalist)
    n_train = int(n_total * (1 - val_split))
    n_val = n_total - n_train

    splits = {"train": [], "val": []}
    limits = {"train": n_train, "val": n_val}
    assigned = set()

    def can_add(split_name):
        return len(splits[split_name]) < limits[split_name]

    def add_item(index, split_name):
        if index in assigned or not can_add(split_name):
            return False
        splits[split_name].append(datalist[index])
        assigned.add(index)
        return True

    label_positive_indices = {
        label: [i for i, item in enumerate(datalist) if item.get(label) == 1] for label in label_names
    }
    labels_by_rarity = sorted(label_names, key=lambda label: len(label_positive_indices[label]))

    for label in labels_by_rarity:
        positive_indices = list(label_positive_indices[label])
        rng.shuffle(positive_indices)

        if not positive_indices:
            if logger is not None:
                logger.warning("No positive samples found for label %s before splitting.", label)
            continue

        target_splits = ["train"]
        if len(positive_indices) >= 2 and n_val > 0:
            target_splits.append("val")

        for split_name in target_splits:
            if any(item.get(label) == 1 for item in splits[split_name]):
                continue
            for index in positive_indices:
                if add_item(index, split_name):
                    break

    remaining_indices = [i for i in range(n_total) if i not in assigned]
    rng.shuffle(remaining_indices)

    for split_name in ("train", "val"):
        for index in remaining_indices:
            if not can_add(split_name):
                break
            add_item(index, split_name)

    if logger is not None:
        logger.info(
            "Using label-aware split with seed=%s: train=%s, val=%s",
            seed,
            len(splits["train"]),
            len(splits["val"])
        )
        _log_split_balance(splits, label_names, logger)

    return splits["train"], splits["val"]


def _log_split_balance(splits, label_names, logger):
    for split_name, split_rows in splits.items():
        logger.info("%s split label balance (%s samples):", split_name.upper(), len(split_rows))
        for label in label_names:
            num_positive = sum(1 for item in split_rows if item.get(label) == 1)
            num_negative = sum(1 for item in split_rows if item.get(label) == 0)
            num_masked = sum(1 for item in split_rows if item.get(label) == -1)
            if num_positive == 0:
                logger.warning("%s split has no positive samples for %s.", split_name.upper(), label)
            logger.info(
                "  %-20s: %4d positive, %4d negative, %4d masked/unknown",
                label,
                num_positive,
                num_negative,
                num_masked,
            )


def _dicoms_for_accession(
    accession_id: str,
    project_id: str = "",
    images_dir: str | None = None,
) -> list[Path]:
    # Local test-data path used by make test-xrays-standard and SITE_DATA.
    root_path = images_dir or os.environ.get("DEV_IMAGES_DIR")
    if root_path:
        root = Path(root_path)
        candidates = []
        # Try accession subfolder first, then recursive match. This is flexible for mini test data.
        for p in [root / str(accession_id), root / f"{accession_id}"]:
            if p.exists():
                candidates.extend(p.rglob("*.dcm"))
        if not candidates and root.exists():
            candidates.extend(root.rglob(f"*{accession_id}*.dcm"))
        if not candidates and root.exists():
            # Fall back to all DICOMs only if there are very few. This avoids total failure on
            # slightly different mini-data layouts.
            all_dicoms = list(root.rglob("*.dcm"))
            if len(all_dicoms) <= 500:
                candidates = all_dicoms
        return sorted(set(candidates))

    # Real FLIP client path.
    if FLIP is not None and project_id:
        flip = FLIP()
        folder = flip.get_by_accession_number(project_id, accession_id, resource_type=[ResourceType.DICOM])
        return sorted(Path(folder).rglob("*.dcm"))

    return []


def build_datalist(
    config: dict | None = None,
    site_name: str | None = None,
    project_id: str | None = None,
    logger=None,
):
    cfg = config or load_config()
    site_cfg = get_site_data_config(cfg, site_name)
    lesions = get_lesions(cfg)
    normal_key = get_normal_key(cfg)
    value_to_numerical = cfg.get("value_to_numerical", {"1": "Yes", "0": "No"})
    df = _read_dataframe(site_cfg.dataframe)
    accession_col = _find_accession_column(df)
    project_id = project_id if project_id is not None else os.environ.get("PROJECT_ID", "")

    if logger is not None:
        logger.info(
            "Loading xray data for site=%s dataframe=%s images_dir=%s",
            site_cfg.site_name,
            site_cfg.dataframe,
            site_cfg.images_dir,
        )

    datalist = []
    seen_paths = set()
    for _, row in df.iterrows():
        accession_id = str(row[accession_col])
        labels = get_labels_from_radiology_row(row, lesions, value_to_numerical, normal_key)
        for img in _dicoms_for_accession(accession_id, project_id=project_id, images_dir=site_cfg.images_dir):
            if img in seen_paths:
                continue
            try:
                _ = pydicom.dcmread(str(img), stop_before_pixels=True)
            except Exception:
                continue
            item = {"image": str(img)}
            item.update(labels)
            datalist.append(item)
            seen_paths.add(img)

    if not datalist:
        raise RuntimeError(
            "No DICOM image/label pairs found for "
            f"site={site_cfg.site_name!r}. Check images_dir={site_cfg.images_dir!r}, "
            f"dataframe={site_cfg.dataframe!r}, and accession ids."
        )

    val_split = float(cfg.get("VAL_SPLIT", 0.2))
    split_seed = int(cfg.get("SPLIT_SEED", 42))
    train_items, val_items = _label_aware_split(
        datalist=datalist,
        label_names=lesions.get_lesion_list(),
        val_split=val_split,
        seed=split_seed,
        logger=logger,
    )
    return train_items, val_items



