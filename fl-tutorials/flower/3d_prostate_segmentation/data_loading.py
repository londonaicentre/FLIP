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

from dataset import IMAGE_KEY, PZ_TZ_KEY, WHOLE_GLAND_KEY, build_loader, load_case

SEED = 42


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
        self.dataframe = self.flip.get_dataframe(
            project_id=self.project_id, query=self.query
        )
        log(INFO, f"FLIP dataframe has {len(self.dataframe)} rows.")

    def get_case_list(
        self, modality: str, val_split: float, test_split: float, is_test: bool = False
    ) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return train/val (or test) datalists of `{IMAGE_KEY, WHOLE_GLAND_KEY, PZ_TZ_KEY}` paths.

        Args:
            modality: Which of the study's three scans (`t2w`, `adc`, `hbv`) to use as the image.
                query.sql's `image_occurrence` rows carry all three modalities under one shared
                `accession_id` per its own comment, and neither XNAT's scan resource filenames
                (`input_<scan_id>.nii.gz`, numbered, not named) nor `flip.xnat`'s `XnatScan` carry a
                series/modality tag. So there is currently no signal to pick "the t2w one" out of an
                accession's pulled files — this raises when an accession pulls back more than one
                `input_*.nii.gz`, rather than silently guessing. Needs either a modality column added
                to the cohort query (resolved against XNAT scan metadata) or a `flip` capability to
                fetch one named series.
            val_split: Validation fraction (0-1).
            test_split: Test fraction (0-1).
            is_test: Return only the test split when True; otherwise return `(train, val)`.

        Returns:
            The test datalist, or a `(train, val)` pair of datalists, depending on `is_test`.
        """
        if self.dataframe is None:
            raise RuntimeError(
                "FLIP_BASE.dataframe not populated; call fetch_dataframe() first."
            )

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

            images = list(Path(accession_folder).rglob("input_*.nii.gz"))
            if not images:
                log(INFO, f"⚠️ No input_*.nii.gz for accession_id={accession_id}")
                continue
            if len(images) > 1:
                raise RuntimeError(
                    f"accession_id={accession_id} pulled {len(images)} input_*.nii.gz files "
                    f"{[p.name for p in images]} — one per scan (t2w/adc/hbv). Selecting "
                    f"modality={modality!r} among them isn't possible today; see get_case_list's "
                    "docstring."
                )
            image_path = images[0]
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
) -> Dataset:
    """Wrap a `get_case_list` datalist in a `monai.data.Dataset`, reusing dataset.py's loader.

    Args:
        datalist: `{IMAGE_KEY, WHOLE_GLAND_KEY, PZ_TZ_KEY, "accession_id"}` dicts from `get_case_list`.
        transform: Applied to the `{"image", "mask", "accession_id"}` dict after loading — e.g.
            `preprocess.build_case_transform(...)`. None for the raw loaded case.
        target_spacing: Passed straight to `dataset.build_loader` — see its docstring.

    Returns:
        Dataset: `dataset[i]` runs `dataset.py`'s load/orient/resample/combine-masks logic, then
        `transform`, exactly like `PicaiDataset.__getitem__` — just sourced from a datalist of pulled
        file paths instead of a `site_dir` scan.
    """
    loader = build_loader(target_spacing)

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

    return Dataset(
        data=datalist,
        transform=Compose([_load, transform]) if transform is not None else _load,
    )
