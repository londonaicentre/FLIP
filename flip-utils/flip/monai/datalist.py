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

"""Build a MONAI datalist from a FLIP cohort and an overlay's imaging binding (FLIP#1106).

Generalises ``build_datalist`` in the NVFLARE spleen tutorial
(``fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation/app_files/trainer.py``) and its
drifted Flower twin ``get_image_and_label_list``: the globs, the pairing and the QC there are
hard-coded for ``input_*`` / ``label_*``; here they come from ``flip.imaging`` in ``monai-flip.yaml``.

Pairing rule. Every key's pattern holds exactly one ``*`` (enforced by the overlay schema). The
``image`` pattern is globbed recursively under each accession folder; the text its ``*`` matched is
the *stem*, and each other key must have exactly the file ``pattern.replace("*", stem)`` in the
**same directory**. Pairing is therefore a substitution, not a second search, so "several candidate
labels" cannot happen; a missing sibling skips that image and is counted. With the spleen overlay
(``input_*.nii.gz`` / ``label_*.nii.gz``) this is exactly the tutorial's
``str(img).replace("/input_", "/label_")``.

QC, carried over from the tutorial: every file of an item must load with nibabel, the image must have
``expected_ndim`` dimensions (default 3; ``None`` skips the check), and every other key must have the
image's shape. A cohort that yields nothing fails loudly with the counts that explain why, because
the alternative is torch's ``num_samples=0`` several frames later, which reads like an app bug.

v0.1 reads the ``NIFTI`` resource only. The schema accepts ``SEG`` and ``DICOM`` so overlays need not
change when they are supported, but this module refuses them by name rather than guessing a layout.

The query is the caller's: on the platform it is the project's approved cohort, delivered in the
client config, never a file inside the bundle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from flip.constants.flip_constants import ResourceType
from flip.monai.overlay import ImagingBinding

if TYPE_CHECKING:
    import pandas as pd

    from flip.core.base import FLIPBase

logger = logging.getLogger(__name__)

IMAGE_KEY = "image"
SUPPORTED_RESOURCES = frozenset({ResourceType.NIFTI})


class DatalistError(RuntimeError):
    """The cohort produced no usable items, or the binding cannot be served."""


@dataclass
class DatalistReport:
    """Why accessions and images were dropped. Logged, and quoted in :class:`DatalistError`."""

    accessions: int = 0
    fetch_failed: int = 0
    images_found: int = 0
    missing_sibling: dict[str, int] = field(default_factory=dict)
    unreadable: int = 0
    wrong_ndim: int = 0
    shape_mismatch: int = 0
    items: int = 0

    def summary(self) -> str:
        """One line naming every non-zero counter."""
        missing = ", ".join(f"no {k} for {n}" for k, n in sorted(self.missing_sibling.items()) if n)
        parts = [
            f"{self.items} item(s) from {self.images_found} image(s) across {self.accessions} accession(s)",
            f"{self.fetch_failed} accession(s) failed to download" if self.fetch_failed else "",
            missing,
            f"{self.unreadable} unreadable" if self.unreadable else "",
            f"{self.wrong_ndim} wrong dimensionality" if self.wrong_ndim else "",
            f"{self.shape_mismatch} shape mismatch" if self.shape_mismatch else "",
        ]
        return "; ".join(p for p in parts if p)


def stem_of(name: str, pattern: str) -> str | None:
    """Return the text ``pattern``'s single ``*`` matches in ``name``, or ``None`` if it does not match."""
    prefix, suffix = pattern.split("*")
    if len(name) < len(prefix) + len(suffix) or not name.startswith(prefix) or not name.endswith(suffix):
        return None
    return name[len(prefix) : len(name) - len(suffix)]


def pair_siblings(image: Path, binding: ImagingBinding) -> tuple[dict[str, str] | None, str | None]:
    """Resolve every key for one image file.

    Returns:
        tuple: ``(item, None)`` when every sibling exists, else ``(None, missing_key)``.
    """
    stem = stem_of(image.name, binding.keys[IMAGE_KEY].pattern)
    if stem is None:  # rglob matched, so this only happens if the caller passes a foreign file
        raise ValueError(f"{image.name} does not match the image pattern")
    item = {IMAGE_KEY: str(image)}
    for key, spec in binding.keys.items():
        if key == IMAGE_KEY:
            continue
        sibling = image.with_name(spec.pattern.replace("*", stem))
        if not sibling.is_file():
            return None, key
        item[key] = str(sibling)
    return item, None


def _qc(item: dict[str, str], expected_ndim: int | None, report: DatalistReport) -> bool:
    import nibabel as nib  # flip-utils[full]; imported here so the module loads without it

    try:
        shapes = {key: tuple(nib.load(path).shape) for key, path in item.items()}  # type: ignore[attr-defined]
    except (nib.filebasedimages.ImageFileError, OSError) as err:
        logger.info("Unreadable item %s: %s", item[IMAGE_KEY], err)
        report.unreadable += 1
        return False
    image_shape = shapes[IMAGE_KEY]
    if expected_ndim is not None and len(image_shape) != expected_ndim:
        logger.info("Skipping %s: %dD, expected %dD", item[IMAGE_KEY], len(image_shape), expected_ndim)
        report.wrong_ndim += 1
        return False
    mismatched = {k: s for k, s in shapes.items() if s != image_shape}
    if mismatched:
        logger.info("Shape mismatch for %s: image=%s others=%s", item[IMAGE_KEY], image_shape, mismatched)
        report.shape_mismatch += 1
        return False
    return True


def build_datalist(
    flip: FLIPBase,
    project_id: str,
    dataframe: pd.DataFrame,
    binding: ImagingBinding,
    *,
    expected_ndim: int | None = 3,
) -> tuple[list[dict[str, str]], DatalistReport]:
    """Walk every accession in the cohort and return the QC-passing datalist items.

    Args:
        flip: The FLIP client (``FLIP()``), used for ``get_by_accession_number``.
        project_id: The FLIP project id.
        dataframe: The cohort, from ``flip.get_dataframe(project_id, query)``; needs ``accession_id``.
        binding: The overlay's ``flip.imaging`` block.
        expected_ndim: Required image dimensionality; ``None`` disables the check.

    Returns:
        tuple: The items (one dict per image, keyed like ``binding.keys``) and the drop report.

    Raises:
        DatalistError: If the resource is not supported in v0.1, the cohort has no ``accession_id``
            column, or no item survives. The message carries the report.
    """
    if binding.resource not in SUPPORTED_RESOURCES:
        raise DatalistError(
            f"flip.imaging.resource={binding.resource.value} is not supported by the v0.1 datalist; "
            f"supported: {', '.join(sorted(r.value for r in SUPPORTED_RESOURCES))}"
        )
    if "accession_id" not in dataframe.columns:
        raise DatalistError("the cohort dataframe has no 'accession_id' column; the imaging join needs it")

    report = DatalistReport(missing_sibling={k: 0 for k in binding.keys if k != IMAGE_KEY})
    items: list[dict[str, str]] = []
    image_pattern = binding.keys[IMAGE_KEY].pattern

    for accession_id in dataframe["accession_id"]:
        report.accessions += 1
        try:
            folder = flip.get_by_accession_number(project_id, str(accession_id), resource_type=binding.resource)
        except Exception as err:  # one bad accession must not end the run; it is counted instead
            logger.info("Could not fetch images for accession_id=%s: %s", accession_id, err)
            report.fetch_failed += 1
            continue

        for image in sorted(Path(folder).rglob(image_pattern)):
            if not image.is_file():
                continue
            report.images_found += 1
            item, missing = pair_siblings(image, binding)
            if item is None:
                logger.info("No %s for %s", missing, image.name)
                report.missing_sibling[missing] += 1  # type: ignore[index]
                continue
            if _qc(item, expected_ndim, report):
                items.append(item)

    report.items = len(items)
    if not items:
        hint = ""
        if report.images_found and any(report.missing_sibling.values()):
            keys = ", ".join(k for k, n in report.missing_sibling.items() if n)
            hint = f" Images were found but their {keys} files were not; was the data-enrichment (upload) step run?"
        raise DatalistError(f"No usable items: {report.summary()}.{hint}")
    logger.info("Datalist ready: %s", report.summary())
    return items, report
