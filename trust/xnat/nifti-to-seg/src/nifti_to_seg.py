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

"""XNAT Container Service command: turn a scan's NIfTI label masks into DICOM-SEG ROI collections OHIF draws.

Launched by the site-wide ResourceEvent subscription that configure-nifti-to-seg.sh registers, when an FL job
stores masks on a scan with flip.add_resource (FLIP#1945). The Container Service mounts the resource at
--input and injects XNAT_HOST / XNAT_USER / XNAT_PASS for the user the command runs as. Every NIfTI in the
resource becomes one DICOM-SEG on the scan's DICOM series, uploaded as an ROI collection, which is what the
XNAT OHIF viewer lists. A resource without a labels.json is left alone: the labels are the job's to name.

labels.json maps each label value to its segment:
    {"5": {"name": "liver", "category": ["123037004", "SCT", "Anatomical Structure"],
           "type": ["10200004", "SCT", "Liver"], "modifier": null, "color": [40, 140, 40]}}
Only the name is required; a label without codes is described as tissue.
"""

import argparse
import io
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

import highdicom as hd
import numpy as np
import pydicom
import requests
import SimpleITK as sitk
from pydicom.sr.codedict import codes

VERSION = "1.0.0"
# /experiments/<session>/scans/<scan>/resources/<id>: the Container Service's URI of a scan resource.
SCAN_RESOURCE = re.compile(r"/experiments/(?P<experiment>[^/]+)/scans/(?P<scan>[^/]+)/resources/")
TISSUE = ("85756007", "SCT", "Tissue")


class Xnat:
    """The four XNAT calls this command makes; any HTTP error raises, so a failure is a failed container."""

    def __init__(self, host: str, user: str, password: str) -> None:
        self.host = host.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (user, password)

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        response = self.session.request(method, f"{self.host}{path}", timeout=600, **kwargs)
        response.raise_for_status()
        return response

    def project_of(self, experiment: str) -> str:
        items = self._request("GET", f"/data/experiments/{experiment}", params={"format": "json"}).json()["items"]
        return items[0]["data_fields"]["project"]

    def download_dicom(self, experiment: str, scan: str, target: Path) -> None:
        path = f"/data/experiments/{experiment}/scans/{scan}/resources/DICOM/files"
        archive = zipfile.ZipFile(io.BytesIO(self._request("GET", path, params={"format": "zip"}).content))
        for name in archive.namelist():
            if not name.endswith("/"):
                (target / Path(name).name).write_bytes(archive.read(name))

    def store_roi_collection(self, project: str, experiment: str, label: str, seg: bytes) -> None:
        # The OHIF viewer plugin's ROI API, which registers the SEG as an assessor on the session.
        self._request(
            "PUT",
            f"/xapi/roi/projects/{project}/sessions/{experiment}/collections/{label}",
            params={"type": "SEG", "overwrite": "true"},
            data=seg,
            headers={"Content-Type": "application/octet-stream"},
        )


def rgb_to_dicom_lab(rgb: list[int]) -> list[int]:
    """sRGB (0-255) to CIELab (D65) scaled to DICOM's unsigned 16-bit RecommendedDisplayCIELabValue."""
    linear = [((c / 255 + 0.055) / 1.055) ** 2.4 if c / 255 > 0.04045 else c / 255 / 12.92 for c in rgb]
    xyz = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]]) @ linear
    f = [t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116 for t in xyz / [0.95047, 1.0, 1.08883]]
    lab = (116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2]))
    return [round(lab[0] * 65535 / 100), round((lab[1] + 128) * 65535 / 255), round((lab[2] + 128) * 65535 / 255)]


def describe(number: int, label: dict) -> hd.seg.SegmentDescription:
    """One segment's description from its labels.json entry."""
    category = hd.sr.CodedConcept(*label.get("category") or ("123037004", "SCT", "Anatomical Structure"))
    description = hd.seg.SegmentDescription(
        segment_number=number,
        segment_label=label["name"],
        segmented_property_category=category,
        segmented_property_type=hd.sr.CodedConcept(*label.get("type") or TISSUE),
        algorithm_type=hd.seg.SegmentAlgorithmTypeValues.AUTOMATIC,
        algorithm_identification=hd.AlgorithmIdentificationSequence(
            name=label.get("algorithm", "FLIP federated job"), version="1", family=codes.cid7162.ArtificialIntelligence
        ),
    )
    if label.get("modifier"):
        description.SegmentedPropertyTypeCodeSequence[0].SegmentedPropertyTypeModifierCodeSequence = [
            hd.sr.CodedConcept(*label["modifier"])
        ]
    if label.get("color"):
        description.RecommendedDisplayCIELabValue = rgb_to_dicom_lab(label["color"])
    return description


def nifti_to_seg(mask_path: Path, dicom_dir: Path, labels: dict[str, dict], description: str) -> pydicom.Dataset | None:
    """A DICOM-SEG of ``mask_path`` on the series in ``dicom_dir``, or None when no labelled voxel is in it.

    The mask is resampled onto the series' own grid (nearest neighbour), so the SEG is right whatever
    orientation the converter that made the job's NIfTI chose. SimpleITK reads the series in the slice
    order its file list gives, and that same order supplies highdicom's source images, frame for frame.
    """
    reader = sitk.ImageSeriesReader()
    files = reader.GetGDCMSeriesFileNames(str(dicom_dir))
    reader.SetFileNames(files)
    reference = reader.Execute()
    mask = sitk.ReadImage(str(mask_path))
    resampled = sitk.Resample(mask, reference, sitk.Transform(), sitk.sitkNearestNeighbor, 0, sitk.sitkUInt16)
    values = sitk.GetArrayFromImage(resampled)

    present = [int(v) for v in np.unique(values) if v and str(int(v)) in labels]
    if not present:
        return None
    segments = np.zeros(values.shape, dtype=np.uint8)
    for number, value in enumerate(present, start=1):
        segments[values == value] = number

    source = [pydicom.dcmread(f) for f in files]
    seg = hd.seg.Segmentation(
        source_images=source,
        pixel_array=segments,
        segmentation_type=hd.seg.SegmentationTypeValues.BINARY,
        segment_descriptions=[describe(n, labels[str(v)]) for n, v in enumerate(present, start=1)],
        series_instance_uid=hd.UID(),
        series_number=900,
        sop_instance_uid=hd.UID(),
        instance_number=1,
        manufacturer="FLIP",
        manufacturer_model_name="nifti-to-seg",
        software_versions=VERSION,
        device_serial_number="1",
        series_description=description[:64],
    )
    # The XNAT OHIF viewer (3.7) reads a top-level SourceImageSequence, when there is one, as one entry per
    # frame; highdicom writes one per source slice, so frames land on the wrong slices or are dropped. The
    # sequence is optional, and each frame keeps its own reference (DerivationImageSequence).
    if "SourceImageSequence" in seg:
        del seg.SourceImageSequence
    return seg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-i", "--input", required=True, type=Path, help="Mounted resource files")
    parser.add_argument("--resource-uri", required=True, help="The resource's XNAT URI")
    parser.add_argument("--resource-label", required=True, help="The resource's label")
    args = parser.parse_args(argv)

    match = SCAN_RESOURCE.search(args.resource_uri)
    if not match:
        print(f"Nothing to do: {args.resource_uri} is not a scan resource.")
        return 0
    labels_file = args.input / "labels.json"
    if not labels_file.is_file():
        print(f"Nothing to do: resource {args.resource_label} has no labels.json.")
        return 0
    labels = json.loads(labels_file.read_text())
    masks = sorted(p for p in args.input.rglob("*") if p.name.endswith((".nii", ".nii.gz")))
    if not masks:
        print(f"Nothing to do: resource {args.resource_label} holds no NIfTI.")
        return 0

    experiment, scan = match["experiment"], match["scan"]
    xnat = Xnat(os.environ["XNAT_HOST"], os.environ["XNAT_USER"], os.environ["XNAT_PASS"])
    project = xnat.project_of(experiment)
    with tempfile.TemporaryDirectory() as tmp:
        dicom_dir = Path(tmp)
        xnat.download_dicom(experiment, scan, dicom_dir)
        for index, mask in enumerate(masks):
            # One collection per mask, named after the session too: XNAT wants an assessor label unique in its
            # project. Overwrite, so a re-run replaces its own collection rather than adding one.
            label = f"{experiment}_{args.resource_label}_{scan}" + (f"_{index + 1}" if len(masks) > 1 else "")
            seg = nifti_to_seg(mask, dicom_dir, labels, f"{args.resource_label} {mask.name}")
            if seg is None:
                print(f"Skipped {mask.name}: no labelled voxel on scan {scan}.")
                continue
            buffer = io.BytesIO()
            seg.save_as(buffer)
            xnat.store_roi_collection(project, experiment, label, buffer.getvalue())
            print(f"Stored {mask.name} as ROI collection {label}: {len(seg.SegmentSequence)} segments.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
