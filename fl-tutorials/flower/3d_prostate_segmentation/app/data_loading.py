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

from __future__ import annotations

import re
from collections.abc import Callable
from logging import INFO
from pathlib import Path
from typing import Any

import numpy as np
import torch
from flip import FLIP
from flip.constants import ResourceType
from flwr.common import log
from monai.data import Dataset
from monai.transforms import Compose

from app.dataset import IMAGE_KEY, PZ_TZ_KEY, WHOLE_GLAND_KEY, build_loader, load_case

SEED = 42

# The DICOM SeriesNumber each PI-CAI modality was written with (datasets/prostate/
# convert_mha_to_dicom.py, MODALITY_UID_COMPONENT). XNAT numbers a session's scans by SeriesNumber,
# and its export nests each scan's files under ``scans/<scan id>-<series description>/``, so the
# folder a pulled ``input_*.nii.gz`` sits in says which series it is.
SERIES_NUMBER = {"t2w": 1, "adc": 2, "hbv": 3}
_SCAN_DIR = re.compile(r"^(\d+)(?:-|$)")


def select_series(accession_dir: Path, modality: str) -> Path:
    """Pick the one ``input_*.nii.gz`` of an accession that is the requested series.

    A prostate study pulls three scans (t2w, adc, hbv) — one ``input_<...>.nii.gz`` each, every one
    with the same ``label_``/``zonal_`` masks beside it after enrichment — and the app trains on one
    of them. Three signals identify it, tried in order, and a signal that matches more than one file
    is ambiguous and raises rather than guessing:

    1. **The scan folder.** On the platform the pulled tree is XNAT's export layout,
       ``<session>/scans/<scan id>-<description>/resources/NIFTI/files/``, and the scan id is the
       DICOM SeriesNumber (``SERIES_NUMBER``). The simulator layout mirrors it.
    2. **The file name.** A ``_<modality>`` token in the stem (``input_<accession>_t2w.nii.gz``), the
       local conversion's naming.
    3. **Only one candidate**, whatever it is called.

    Args:
        accession_dir: The folder ``flip.get_by_accession_number`` returned.
        modality: ``"t2w"``, ``"adc"`` or ``"hbv"``.

    Returns:
        The selected image path.

    Raises:
        FileNotFoundError: No ``input_*.nii.gz`` under ``accession_dir`` at all (the blank folder
            LOCAL_DEV creates for an accession it has no data for).
        RuntimeError: Several candidates and no signal singles one out, or a signal matches several.
    """
    if modality not in SERIES_NUMBER:
        raise ValueError(f"unknown modality {modality!r}; expected one of {sorted(SERIES_NUMBER)}")
    candidates = sorted(Path(accession_dir).rglob("input_*.nii.gz"))
    if not candidates:
        raise FileNotFoundError(f"no input_*.nii.gz under {accession_dir}")

    def _ambiguous(rule: str, matches: list[Path]) -> RuntimeError:
        listing = ", ".join(str(m.relative_to(accession_dir)) for m in matches)
        return RuntimeError(
            f"{rule} matches {len(matches)} scans for modality={modality!r} under {accession_dir}: {listing}"
        )

    series_number = SERIES_NUMBER[modality]
    by_folder = [
        path
        for path in candidates
        if any(
            (m := _SCAN_DIR.match(part)) and int(m.group(1)) == series_number
            for part in path.relative_to(accession_dir).parts[:-1]
        )
    ]
    if len(by_folder) == 1:
        return by_folder[0]
    if len(by_folder) > 1:
        raise _ambiguous(f"scan folder {series_number}-*", by_folder)

    by_name = [path for path in candidates if modality in path.name.removesuffix(".nii.gz").split("_")]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise _ambiguous(f"file name token _{modality}", by_name)

    if len(candidates) == 1:
        return candidates[0]
    listing = ", ".join(str(c.relative_to(accession_dir)) for c in candidates)
    raise RuntimeError(
        f"{len(candidates)} input_*.nii.gz under {accession_dir} and none identifiable as modality={modality!r} "
        f"(no scan folder named {series_number}-*, no _{modality} file-name token): {listing}"
    )


class FLIP_BASE:
    """Fetches the cohort dataframe and pulls each accession's image + masks from XNAT."""

    def __init__(self) -> None:
        self.project_id: str = ""
        self.query: str = ""
        self.dataframe = None
        self.flip = FLIP()

    def fetch_dataframe(self) -> None:
        """Populate `self.dataframe` from the FLIP cohort query (reads project_id/query)."""
        log(
            INFO,
            f"Fetching FLIP dataframe project_id={self.project_id} query={self.query}",
        )
        self.dataframe = self.flip.get_dataframe(project_id=self.project_id, query=self.query)
        log(INFO, f"FLIP dataframe has {len(self.dataframe)} rows.")

    def get_case_list(
        self, modality: str, val_split: float, test_split: float, is_test: bool = False
    ) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return train/val (or test) datalists of `{IMAGE_KEY, WHOLE_GLAND_KEY, PZ_TZ_KEY}` paths.

        Args:
            modality: Which of the study's three scans (`t2w`, `adc`, `hbv`) to train on. query.sql
                returns one row per series under one shared `accession_id`, so each study is pulled
                once and `select_series` picks the requested scan out of the pulled files.
            val_split: Validation fraction (0-1).
            test_split: Test fraction (0-1).
            is_test: Return only the test split when True; otherwise return `(train, val)`.

        Returns:
            The test datalist, or a `(train, val)` pair of datalists, depending on `is_test`.
        """
        if self.dataframe is None:
            raise RuntimeError("FLIP_BASE.dataframe not populated; call fetch_dataframe() first.")

        datalist = []
        # One row per modality per study (see query.sql); pull each study's files once.
        for accession_id in self.dataframe["accession_id"].unique():
            try:
                accession_folder = self.flip.get_by_accession_number(
                    self.project_id, accession_id, resource_type=[ResourceType.NIFTI]
                )
            except Exception as err:
                log(
                    INFO,
                    f"⚠️ Could not fetch images for accession_id={accession_id}: {err}",
                )
                continue

            try:
                image_path = select_series(Path(accession_folder), modality)
            except FileNotFoundError:
                log(INFO, f"⚠️ No input_*.nii.gz for accession_id={accession_id}")
                continue
            whole_gland_path = Path(str(image_path).replace("/input_", "/label_"))
            pz_tz_path = Path(str(image_path).replace("/input_", "/zonal_"))
            if not whole_gland_path.exists() or not pz_tz_path.exists():
                log(
                    INFO,
                    f"⚠️ No matching label(s) for accession_id={accession_id} — was data enrichment run?",
                )
                continue

            datalist.append(
                {
                    IMAGE_KEY: image_path,
                    WHOLE_GLAND_KEY: whole_gland_path,
                    PZ_TZ_KEY: pz_tz_path,
                    "accession_id": accession_id,
                }
            )

        log(INFO, f"Dataset ready: {len(datalist)} case(s)")
        if not datalist:
            raise RuntimeError(
                f"No usable cases found across {len(self.dataframe['accession_id'].unique())} accession(s). "
                "Was the data-enrichment step (upload_prostate_labels_to_xnat.py) run after the image pull?"
            )

        n_total = len(datalist)
        n_test = int(test_split * n_total)
        n_val = int(val_split * n_total)
        n_train = n_total - n_val - n_test

        rng = np.random.default_rng(seed=SEED)
        rng.shuffle(datalist)

        train_datalist = datalist[:n_train]
        val_datalist = datalist[n_train : n_train + n_val]
        test_datalist = datalist[n_train + n_val :]

        if is_test:
            return test_datalist
        return train_datalist, val_datalist


def build_dataset(
    datalist: list[dict[str, Any]],
    transform: Compose | None,
    target_spacing: tuple[float, float, float] | None = None,
    patch_iter: Callable | None = None,
) -> Dataset:
    """Wrap a `get_case_list` datalist in a `monai.data.Dataset`, reusing dataset.py's loader.

    Args:
        datalist: `{IMAGE_KEY, WHOLE_GLAND_KEY, PZ_TZ_KEY, "accession_id"}` dicts from `get_case_list`.
        transform: Applied to the `{"image", "mask", "accession_id"}` dict after loading — e.g.
            `preprocess.build_case_transform(...)`. None for the raw loaded case.
        target_spacing: Passed straight to `dataset.build_loader` — see its docstring.
        patch_iter: A `monai.transforms.PatchIterd`; when given, `dataset[i]` is the LIST of that
            case's patches (each carrying `coord`, `img_shape`, `mask_shape`), which
            `monai.data.list_data_collate` flattens into one batch — the same tiling
            `PicaiDataset.__getitem__` does for the standalone trainer.

    Returns:
        Dataset: `dataset[i]` runs `dataset.py`'s load/orient/resample/combine-masks logic, then
        `transform`, exactly like `PicaiDataset.__getitem__` — just sourced from a datalist of pulled
        file paths instead of a `site_dir` scan.
    """
    loader = build_loader(target_spacing)

    def _patchify(data: dict[str, Any]) -> list[dict[str, Any]]:
        img_shape, mask_shape = tuple(data[IMAGE_KEY].shape), tuple(data["mask"].shape)
        return [
            {
                IMAGE_KEY: patch[IMAGE_KEY],
                "mask": patch["mask"],
                "accession_id": data["accession_id"],
                "coord": coord,
                "img_shape": img_shape,
                "mask_shape": mask_shape,
            }
            for patch, coord in patch_iter(data)
        ]

    def _load(item: dict[str, Any]) -> dict[str, Any]:
        image, mask = load_case(
            {
                IMAGE_KEY: item[IMAGE_KEY],
                WHOLE_GLAND_KEY: item[WHOLE_GLAND_KEY],
                PZ_TZ_KEY: item[PZ_TZ_KEY],
            },
            loader,
        )
        return {
            "image": image.as_tensor().to(torch.float32),
            "mask": mask.to(torch.float16),
            "accession_id": item["accession_id"],
        }

    steps: list[Callable] = [_load]
    if transform is not None:
        steps.append(transform)
    if patch_iter is not None:
        # Last, deliberately: Compose maps any later transform over a list output.
        steps.append(_patchify)
    return Dataset(data=datalist, transform=Compose(steps) if len(steps) > 1 else _load)
