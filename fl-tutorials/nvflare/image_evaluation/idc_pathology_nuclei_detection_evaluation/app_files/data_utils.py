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
"""Resolve this FL client's slides, and load each with its reference annotations.

Two data paths, selected by ``FlipConstants.LOCAL_DEV``, matching the other FLIP evaluation tutorials:

* **Federated client** -- the cohort comes from the trust's OMOP database via ``flip.get_dataframe``
  and the imaging from XNAT via ``flip.get_by_accession_number``.
* **Simulator / LOCAL_DEV** -- the per-site CSV and image directory are read directly from the
  ``SITE<N>_*`` environment variables the Makefile exports from ``.env.app``.

The dev path deliberately does **not** go through ``flip.get_dataframe``. That reads
``FlipConstants.DEV_DATAFRAME``, which the settings singleton captures once at import, so a
per-site value assigned later in the process would be ignored and every site would silently score
the same slides -- which for a cross-site comparison is the worst possible failure, because it
produces plausible numbers rather than an error.

Site identity comes from ``flare.get_site_name()``, never from a variable naming a hospital.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from annotations import load_reference_nuclei
from dicom_wsi import SlideReader
from flip.constants import FlipConstants, ResourceType

logger = logging.getLogger(__name__)

SLIDE_FILENAME = "slide.dcm"
ANNOTATION_FILENAME = "annotation.dcm"


@dataclass(frozen=True)
class SlideCase:
    """One slide and its reference annotations, ready to score.

    Attributes:
        accession_id: FLIP accession identifier -- the slide's SeriesInstanceUID here.
        patient_id: TCGA barcode. Tiles from one patient are not independent samples, so metrics are
            grouped by patient to get an honest spread.
        reader: Random-access reader for the slide's pixels.
        reference_centroids: ``(N, 2)`` reference nucleus centres in total-pixel-matrix coordinates.
        generation_type: ``AUTOMATIC`` for Pan-Cancer-Nuclei-Seg -- reference detections, not truth.
    """

    accession_id: str
    patient_id: str
    reader: SlideReader
    reference_centroids: np.ndarray
    generation_type: str


def is_local_dev() -> bool:
    """True in the simulator, where per-site paths come from the environment."""
    return bool(FlipConstants.LOCAL_DEV)


def _site_prefix(site_name: str) -> str:
    """Map an NVFLARE site name to its environment prefix: ``site-1`` -> ``SITE1``."""
    return site_name.replace("-", "").replace("_", "").upper() if site_name else ""


def resolve_site_paths(site_name: str) -> tuple[Path, Path]:
    """Return this site's ``(dataframe_csv, images_dir)`` from the environment.

    Falls back to ``DEV_DATAFRAME`` / ``DEV_IMAGES_DIR`` for a single-site run.

    Raises:
        RuntimeError: If neither the per-site nor the fallback variables are set. Named explicitly so
            the failure says which site is unconfigured, instead of surfacing later as an empty path.
    """
    prefix = _site_prefix(site_name)
    dataframe = (os.environ.get(f"{prefix}_DATAFRAME") if prefix else None) or os.environ.get("DEV_DATAFRAME")
    images_dir = (os.environ.get(f"{prefix}_IMAGES_DIR") if prefix else None) or os.environ.get("DEV_IMAGES_DIR")

    if not dataframe or not images_dir:
        raise RuntimeError(
            f"No data paths configured for site {site_name!r}. Set {prefix or 'SITE<N>'}_DATAFRAME and "
            f"{prefix or 'SITE<N>'}_IMAGES_DIR (or DEV_DATAFRAME and DEV_IMAGES_DIR) in .env.app, then "
            "run 'make -C fl-tutorials download-idc-pathology-data' if the data is missing."
        )
    return Path(dataframe), Path(images_dir)


def load_cohort(flip, project_id: str, query: str, site_name: str) -> pd.DataFrame:
    """Fetch this site's cohort of slides.

    Raises:
        ValueError: If the cohort has no ``accession_id`` column, which every FLIP dataframe must
            carry for ``get_by_accession_number`` to resolve imaging.
    """
    if is_local_dev():
        dataframe_path, _ = resolve_site_paths(site_name)
        cohort = pd.read_csv(dataframe_path)
        logger.info("%s: read %d slide(s) from %s", site_name, len(cohort), dataframe_path)
    else:
        cohort = flip.get_dataframe(project_id, query)
        logger.info("%s: cohort query returned %d slide(s)", site_name, len(cohort))

    if "accession_id" not in cohort.columns:
        raise ValueError(f"{site_name}: cohort has no 'accession_id' column (got {list(cohort.columns)}).")
    return cohort


def _accession_dir(flip, project_id: str, accession_id: str, site_name: str) -> Path:
    """Return the directory holding this accession's DICOM files."""
    if is_local_dev():
        _, images_dir = resolve_site_paths(site_name)
        return images_dir / accession_id
    return Path(flip.get_by_accession_number(project_id, accession_id, [ResourceType.DICOM]))


def load_slide_case(flip, project_id: str, accession_id: str, patient_id: str, site_name: str) -> SlideCase:
    """Load one slide and its reference annotations.

    Raises:
        FileNotFoundError: If either DICOM object is missing, naming the command that fetches them.
    """
    accession_dir = _accession_dir(flip, project_id, accession_id, site_name)
    slide_path = accession_dir / SLIDE_FILENAME
    annotation_path = accession_dir / ANNOTATION_FILENAME

    for path in (slide_path, annotation_path):
        if not path.exists():
            raise FileNotFoundError(
                f"{accession_id}: expected {path.name} in {accession_dir}. Fetch the tutorial data with "
                "'make -C fl-tutorials download-idc-pathology-data'."
            )

    reference = load_reference_nuclei(annotation_path)
    return SlideCase(
        accession_id=accession_id,
        patient_id=patient_id,
        reader=SlideReader(slide_path),
        reference_centroids=reference.centroids,
        generation_type=reference.generation_type,
    )
