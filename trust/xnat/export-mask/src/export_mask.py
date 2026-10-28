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
#
"""XNAT Container Service command: convert an OHIF DICOM-SEG assessor to NIfTI and upload it back.

Launched by the site-wide ImageAssessorEvent subscription that configure-export-mask.sh registers
(MONAI Label trusts only). The Container Service mounts the assessor's SEG resource at --input and
injects XNAT_HOST / XNAT_USER / XNAT_PASS for the user the command runs as. Each DICOM-SEG becomes a
`.nii.gz` beside it in the assessor's SEG resource, resampled onto the session's NIfTI scan when
there is exactly one, so the mask lines up voxel for voxel with the image FL training reads.
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

import highdicom as hd
import numpy as np
import requests
import SimpleITK as sitk


def dicom_seg_to_nifti(dicom_seg: Path, output: Path) -> None:
    """Write one DICOM-SEG as a multi-class label image (0 = background, n = segment n).

    highdicom returns the labels with an affine from array indices to patient (LPS) millimetres;
    SimpleITK indexes the same array as (x, y, z) = (axis 2, axis 1, axis 0), so its spacing,
    direction and origin are read off the affine's columns in that order.
    """
    volume = hd.seg.segread(dicom_seg).get_volume(combine_segments=True, dtype=np.uint8)
    axes = volume.affine[:3, [2, 1, 0]]
    spacing = np.linalg.norm(axes, axis=0)
    image = sitk.GetImageFromArray(volume.array)
    image.SetSpacing(spacing.tolist())
    image.SetDirection((axes / spacing).flatten().tolist())
    image.SetOrigin(volume.affine[:3, 3].tolist())
    sitk.WriteImage(image, str(output), True)


def resample_to_reference(mask: Path, reference: Path) -> None:
    """Resample ``mask`` in place onto ``reference``'s grid; nearest neighbour keeps labels integral."""
    resampled = sitk.Resample(
        sitk.ReadImage(str(mask)), sitk.ReadImage(str(reference)), sitk.Transform(), sitk.sitkNearestNeighbor, 0
    )
    sitk.WriteImage(resampled, str(mask), True)


class Xnat:
    """The XNAT REST calls this command makes, as the user the Container Service runs it for.

    Plain requests rather than pyxnat: pyxnat's Interface imports its "derivatives" plugins on
    construction, which need nibabel and pandas it does not declare, and this command needs five
    calls. A missing resource lists as empty, as it did through pyxnat; any other HTTP error raises,
    so a failed run is a failed container in XNAT's command history.
    """

    def __init__(self, host: str, user: str, password: str) -> None:
        self.host = host.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (user, password)

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        response = self.session.request(method, f"{self.host}{path}", timeout=300, **kwargs)
        response.raise_for_status()
        return response

    def list(self, path: str, field: str) -> list[str]:
        try:
            rows = self._request("GET", path, params={"format": "json"}).json()["ResultSet"]["Result"]
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 404:
                return []
            raise
        return [row[field] for row in rows]

    def download(self, path: str, target: Path) -> None:
        with self._request("GET", path, stream=True) as response, target.open("wb") as out:
            for chunk in response.iter_content(chunk_size=1 << 20):
                out.write(chunk)

    def upload(self, path: str, source: Path) -> None:
        with source.open("rb") as body:
            self._request("PUT", path, params={"inbody": "true"}, data=body)


def connect() -> Xnat:
    return Xnat(os.environ["XNAT_HOST"], os.environ["XNAT_USER"], os.environ["XNAT_PASS"])


def find_reference_nifti(xnat: Xnat, experiment: str, workdir: Path) -> Path | None:
    """Download the session's NIfTI scan, or return None when there is not exactly one candidate.

    The OHIF mask covers one series, so with several scans the right reference is ambiguous; the
    mask is then kept in the DICOM-SEG's own geometry rather than resampled onto a guess. Within
    the scan's NIFTI resource the image is dcm2niix's output, named after the Container Service's
    /input mount (``input_*``); enrichment puts ``label_*`` and other masks beside it. Paths are
    experiment-scoped (IDs are unique across XNAT): under /data/projects/<p>/ the same listing
    answers with the experiment's whole item tree instead of a ResultSet.
    """
    session = f"/data/experiments/{experiment}"
    scans = xnat.list(f"{session}/scans", "ID")
    if len(scans) != 1:
        print(f"Not resampling: expected exactly one scan in {experiment}, found {len(scans)}.")
        return None
    files_path = f"{session}/scans/{scans[0]}/resources/NIFTI/files"
    files = [f for f in xnat.list(files_path, "Name") if f.startswith("input_") and f.endswith((".nii", ".nii.gz"))]
    if len(files) != 1:
        print(f"Not resampling: expected exactly one input_*.nii[.gz] on scan {scans[0]}, found {len(files)}.")
        return None
    path = workdir / files[0]
    xnat.download(f"{files_path}/{files[0]}", path)
    return path


def upload(xnat: Xnat, experiment: str, assessor: str, nifti: Path) -> None:
    files_path = f"/data/experiments/{experiment}/assessors/{assessor}/resources/SEG/files"
    if nifti.name in xnat.list(files_path, "Name"):
        # Never overwrite: replacing a mask is a deliberate delete on XNAT, not a side effect of a re-run.
        print(f"Not uploading {nifti.name}: already on assessor {assessor}.")
        return
    xnat.upload(f"{files_path}/{nifti.name}", nifti)
    print(f"Uploaded {nifti.name} to assessor {assessor}.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-i", "--input", required=True, type=Path, help="Mounted SEG resource of the assessor")
    parser.add_argument("-o", "--output", required=True, type=Path, help="Mounted output directory")
    parser.add_argument("--experiment", required=True, help="XNAT session (experiment) ID")
    parser.add_argument("--assessor", required=True, help="XNAT assessor ID")
    args = parser.parse_args(argv)

    seg_files = sorted(args.input.rglob("*.dcm"))
    if not seg_files:
        print(f"No DICOM-SEG (*.dcm) under {args.input}.", file=sys.stderr)
        return 1
    # The output is named by basename and upload() never overwrites, so a second SEG of the same
    # name in another subdirectory would be dropped with only a log line. Refuse instead.
    by_name: dict[str, list[Path]] = {}
    for seg in seg_files:
        by_name.setdefault(seg.name, []).append(seg)
    clashes = [paths for paths in by_name.values() if len(paths) > 1]
    if clashes:
        for paths in clashes:
            print(f"DICOM-SEGs share a name, so their masks would collide: {[str(p) for p in paths]}", file=sys.stderr)
        return 1

    xnat = connect()
    with tempfile.TemporaryDirectory() as workdir:
        reference = find_reference_nifti(xnat, args.experiment, Path(workdir))
        for seg in seg_files:
            nifti = args.output / seg.with_suffix(".nii.gz").name
            dicom_seg_to_nifti(seg, nifti)
            if reference:
                resample_to_reference(nifti, reference)
            upload(xnat, args.experiment, args.assessor, nifti)
    return 0


if __name__ == "__main__":
    sys.exit(main())
