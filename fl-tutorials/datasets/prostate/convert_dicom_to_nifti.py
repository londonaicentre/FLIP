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

"""Convert the prostate DICOM series to NIfTI with the platform's own dcm2niix.

The simulator trains on ``nifti/<patient>/<patient>_<study>_<modality>.nii.gz``; the platform
delivers the same scans to an fl-client as dcm2niix output from XNAT. This step produces the
former *from* the DICOM series that feed the latter, using the exact image the trusts' XNAT
Container Service runs (``ghcr.io/londonaicentre/xnat-dcm2niix:<pin>``, read from the same
``dcm2niix_command.json`` XNAT registers, so there is no second pin to drift). Two consequences:

* The scanner metadata PI-CAI leaves in the ``.mha`` headers is written once — into the DICOM by
  ``convert_mha_to_dicom.py`` — and read back by dcm2niix into a BIDS sidecar here, rather than
  being carried by a second, parallel ``.mha`` → NIfTI converter that could drift.
* What the simulator sees is byte-for-byte what an fl-client sees after image pull, including
  dcm2niix's orientation handling. A direct SimpleITK conversion is *not* that, and this repo has
  already paid for a sideways-image class of bug once (``fl-tutorials/tests/test_dicom_orientation.py``).

Input layout is ``convert_mha_to_dicom.py``'s: ``dicom/<patient>/<study>/<modality>/*.dcm``. One
dcm2niix container per series, in parallel, with the platform's flags (``-z y``) plus ``-b y -ba n``
for the sidecar — an extra file, so the NIfTI bytes are unchanged. Needs Docker; the image is
pulled on first use.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

# The registration XNAT applies to every trust's Container Service. Reading the image from it
# means this step can never run a different dcm2niix than the platform does.
REPO_ROOT = Path(__file__).resolve().parents[3]
DCM2NIIX_COMMAND_JSON = REPO_ROOT / "trust" / "xnat" / "xnat" / "config" / "dcm2niix_command.json"


def platform_dcm2niix_image(command_json: Path = DCM2NIIX_COMMAND_JSON) -> str:
    """The ``ghcr.io/londonaicentre/xnat-dcm2niix:<version>`` the trusts run."""
    return str(json.loads(command_json.read_text())["image"])


def series_dirs(dicom_dir: Path) -> list[tuple[str, str, str, Path]]:
    """Every ``(patient, study, modality, dir)`` under ``dicom/<patient>/<study>/<modality>/``."""
    found = []
    for series in sorted(dicom_dir.glob("*/*/*")):
        if series.is_dir() and any(series.glob("*.dcm")):
            found.append((series.parents[1].name, series.parent.name, series.name, series))
    return found


def dcm2niix_command(image: str, series_dir: Path, out_dir: Path, stem: str) -> list[str]:
    """The ``docker run`` that converts one series, named to the simulator's filename contract."""
    return [
        "docker", "run", "--rm",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{series_dir}:/input:ro",
        "-v", f"{out_dir}:/output",
        image,
        "dcm2niix", "-z", "y", "-b", "y", "-ba", "n", "-f", stem, "-o", "/output", "/input",
    ]  # fmt: skip


def convert_series(image: str, patient: str, study: str, modality: str, series_dir: Path, nifti_dir: Path) -> Path:
    """Convert one series; return the NIfTI path. Skips a series whose output already exists."""
    out_dir = nifti_dir / patient
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{patient}_{study}_{modality}"
    nii_path = out_dir / f"{stem}.nii.gz"
    if nii_path.is_file():
        return nii_path
    result = subprocess.run(dcm2niix_command(image, series_dir, out_dir, stem), capture_output=True, text=True)
    if result.returncode != 0 or not nii_path.is_file():
        raise RuntimeError(f"dcm2niix failed for {series_dir}:\n{result.stdout}\n{result.stderr}")
    return nii_path


def convert_archive(dicom_dir: Path, nifti_dir: Path, workers: int, image: str | None = None) -> None:
    image = image or platform_dcm2niix_image()
    series = series_dirs(dicom_dir)
    if not series:
        raise SystemExit(f"no DICOM series under {dicom_dir} — run convert_mha_to_dicom.py first")
    print(f"Converting {len(series)} series with {image} ({workers} workers)")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(convert_series, image, *entry, nifti_dir) for entry in series]
        for future in tqdm(as_completed(futures), total=len(futures), desc="dcm2niix", unit="series"):
            future.result()


if __name__ == "__main__":
    # fl-tutorials/data/prostate — see download_data.py's default_data_dir comment.
    default_data_dir = Path(__file__).parent.parent.parent / "data" / "prostate"
    parser = argparse.ArgumentParser(
        description="Convert the prostate DICOM series to .nii.gz with the platform's pinned dcm2niix"
    )
    parser.add_argument("--input", type=Path, default=default_data_dir / "dicom")
    parser.add_argument("--output", type=Path, default=default_data_dir / "nifti")
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--image", default=None, help=f"override the image read from {DCM2NIIX_COMMAND_JSON}")
    args = parser.parse_args()
    convert_archive(args.input.resolve(), args.output.resolve(), args.workers, args.image)
