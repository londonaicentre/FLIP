#
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

"""Publish NIfTI segmentation labels already in XNAT as DICOM-SEG ROI collections.

Data enrichment for a segmentation app puts a ``label_*.nii.gz`` beside each converted
``input_*.nii.gz`` in a scan's ``NIFTI`` resource — that is what training consumes, and it
is invisible to a viewer. This script is the display half: it converts each label to
DICOM-SEG against its source series and uploads it as an ROI collection, so the
OHIF-XNAT viewer's Masks panel can import it and draw the segmentation over the images.

Nothing here is required for training. Run it after the label upload, as part of the same
data-enrichment step::

    uv run python -m tests.xnat_seg_upload --flip-project-id <uuid>

Conversion is delegated to dcmqi's ``itkimage2segimage`` in Docker, so no extra Python
dependency is pulled into flip-api. The upload uses the exact contract the viewer's own
export uses (``PUT /xapi/roi/projects/{p}/sessions/{e}/collections/{label}?type=SEG``);
that API ships inside the OHIF viewer plugin, so no additional XNAT plugin is needed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import requests

# Both dev trust XNATs. IPv4 literals on purpose: the XNAT ports are published by Docker
# Swarm ingress, which accepts but never answers ::1, and localhost resolves there first.
DEFAULT_XNAT_URLS = ("http://127.0.0.1:8104", "http://127.0.0.1:8106")
DCMQI_IMAGE = "qiicr/dcmqi:latest"
ROI_COLLECTION_TYPE = "icr:roiCollectionData"


class SegUploadError(RuntimeError):
    """A step of the conversion/upload pipeline failed unrecoverably."""


def _log(message: str) -> None:
    print(message, flush=True)


def _segment_metadata(collection_name: str, segment_label: str) -> dict[str, Any]:
    """Build the dcmqi metadata describing the single segment being encoded.

    Args:
        collection_name (str): SeriesDescription of the SEG — this is the name the
            viewer's Masks > Import dialog lists the collection under.
        segment_label (str): Human-readable label for the segment itself.

    Returns:
        dict[str, Any]: dcmqi ``itkimage2segimage`` metadata document.
    """
    return {
        "ContentCreatorName": "FLIP",
        "ClinicalTrialSeriesID": "1",
        "ClinicalTrialTimePointID": "1",
        "SeriesDescription": collection_name,
        "SeriesNumber": "300",
        "InstanceNumber": "1",
        "segmentAttributes": [
            [
                {
                    "labelID": 1,
                    "SegmentDescription": segment_label,
                    "SegmentLabel": segment_label,
                    "SegmentAlgorithmType": "SEMIAUTOMATIC",
                    "SegmentAlgorithmName": "FLIP-data-enrichment",
                    "SegmentedPropertyCategoryCodeSequence": {
                        "CodeValue": "T-D0050",
                        "CodingSchemeDesignator": "SRT",
                        "CodeMeaning": "Anatomical Structure",
                    },
                    "SegmentedPropertyTypeCodeSequence": {
                        "CodeValue": "T-C3000",
                        "CodingSchemeDesignator": "SRT",
                        "CodeMeaning": segment_label,
                    },
                    "recommendedDisplayRGBValue": [221, 84, 84],
                }
            ]
        ],
    }


def find_project(xnat: requests.Session, xnat_url: str, flip_project_id: str) -> str | None:
    """Find the XNAT project mirroring a FLIP project.

    The imaging import stamps the FLIP project UUID into ``secondary_ID``
    (trust/imaging-api services/projects.py), which is the only stable link between them.

    Args:
        xnat (requests.Session): Authenticated session.
        xnat_url (str): Base URL of the XNAT.
        flip_project_id (str): FLIP project UUID.

    Returns:
        str | None: XNAT project id, or None when this trust has no such project.
    """
    resp = xnat.get(f"{xnat_url}/data/projects?format=json", timeout=120)
    resp.raise_for_status()
    for row in resp.json()["ResultSet"]["Result"]:
        if row.get("secondary_ID") == flip_project_id:
            return str(row["ID"])

    return None


def existing_collections(xnat: requests.Session, xnat_url: str, experiment_id: str) -> list[str]:
    """List the ROI collections already attached to a session.

    Args:
        xnat (requests.Session): Authenticated session.
        xnat_url (str): Base URL of the XNAT.
        experiment_id (str): XNAT experiment (session) id.

    Returns:
        list[str]: Labels of the session's existing ROI collection assessors.
    """
    resp = xnat.get(f"{xnat_url}/data/experiments/{experiment_id}/assessors?format=json", timeout=120)
    if resp.status_code >= 300:
        return []

    return [
        str(row.get("label"))
        for row in resp.json()["ResultSet"]["Result"]
        if row.get("xsiType") == ROI_COLLECTION_TYPE
    ]


def _label_file(xnat: requests.Session, xnat_url: str, scan_uri: str, label_prefix: str) -> str | None:
    """Return the name of the scan's NIfTI label file, if it has one."""
    resp = xnat.get(f"{xnat_url}{scan_uri}/resources/NIFTI/files?format=json", timeout=120)
    if resp.status_code >= 300:
        return None
    for row in resp.json()["ResultSet"]["Result"]:
        name = str(row.get("Name", ""))
        if name.startswith(label_prefix) and name.endswith(".nii.gz"):
            return name

    return None


def _download_series(xnat: requests.Session, xnat_url: str, scan_uri: str, dicom_dir: Path) -> int:
    """Download a scan's DICOM files into ``dicom_dir``, flattened.

    Returns:
        int: Number of DICOM files written.
    """
    resp = xnat.get(f"{xnat_url}{scan_uri}/resources/DICOM/files?format=zip", timeout=600)
    resp.raise_for_status()
    archive = dicom_dir.parent / "dicom.zip"
    archive.write_bytes(resp.content)
    staging = dicom_dir.parent / "dicom_raw"
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(staging)
    count = 0
    # XNAT zips carry the full archive path; dcmqi wants a flat directory of instances.
    for path in staging.rglob("*.dcm"):
        shutil.copy(path, dicom_dir / f"{count:05d}.dcm")
        count += 1

    return count


def _run_dcmqi(work_dir: Path, image: str) -> None:
    """Run dcmqi's itkimage2segimage over a prepared working directory."""
    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-v", f"{work_dir}:/data", "-w", "/data",
            image,
            "itkimage2segimage",
            "--inputImageList", "label.nii.gz",
            "--inputDICOMDirectory", "dicom",
            "--inputMetadata", "meta.json",
            "--outputDICOM", "seg.dcm",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not (work_dir / "seg.dcm").exists():
        raise SegUploadError(f"dcmqi failed: {result.stderr.strip() or result.stdout.strip()}")


def _upload_collection(
    xnat: requests.Session,
    xnat_url: str,
    project_id: str,
    experiment_id: str,
    label: str,
    seg_path: Path,
    overwrite: bool,
) -> str:
    """PUT a DICOM-SEG as an ROI collection, the same way the viewer's own export does."""
    resp = xnat.put(
        f"{xnat_url}/xapi/roi/projects/{project_id}/sessions/{experiment_id}/collections/{label}",
        params={"type": "SEG", "overwrite": str(overwrite).lower()},
        data=seg_path.read_bytes(),
        headers={"Content-Type": "application/octet-stream"},
        timeout=600,
    )
    if resp.status_code >= 300:
        raise SegUploadError(f"ROI collection upload failed: HTTP {resp.status_code} {resp.text[:300]}")

    return resp.text.strip()


def process_experiment(
    xnat: requests.Session,
    xnat_url: str,
    project_id: str,
    experiment: dict[str, Any],
    args: argparse.Namespace,
) -> bool:
    """Convert and upload every labelled scan of one session.

    Args:
        xnat (requests.Session): Authenticated session.
        xnat_url (str): Base URL of the XNAT.
        project_id (str): XNAT project id.
        experiment (dict[str, Any]): Experiment row from the project listing.
        args (argparse.Namespace): Parsed CLI options.

    Returns:
        bool: True when a collection was uploaded for this session.
    """
    experiment_id = str(experiment["ID"])
    label = f"{args.collection_label}_{experiment.get('label', experiment_id)}"
    if not args.overwrite and existing_collections(xnat, xnat_url, experiment_id):
        _log(f"    ↷ {experiment_id}: already has an ROI collection")
        return False

    scans = xnat.get(f"{xnat_url}/data/experiments/{experiment_id}/scans?format=json", timeout=120)
    scans.raise_for_status()
    for scan in scans.json()["ResultSet"]["Result"]:
        scan_uri = str(scan["URI"])
        label_name = _label_file(xnat, xnat_url, scan_uri, args.label_prefix)
        if not label_name:
            continue

        with tempfile.TemporaryDirectory(prefix="flip-seg-") as tmp:
            work_dir = Path(tmp)
            dicom_dir = work_dir / "dicom"
            dicom_dir.mkdir()
            slices = _download_series(xnat, xnat_url, scan_uri, dicom_dir)
            if slices == 0:
                _log(f"    ⚠️  {experiment_id} scan {scan['ID']}: no DICOM files; skipped")
                continue

            label_resp = xnat.get(f"{xnat_url}{scan_uri}/resources/NIFTI/files/{label_name}", timeout=600)
            label_resp.raise_for_status()
            (work_dir / "label.nii.gz").write_bytes(label_resp.content)
            (work_dir / "meta.json").write_text(
                json.dumps(_segment_metadata(args.collection_name, args.segment_label))
            )

            _run_dcmqi(work_dir, args.dcmqi_image)
            collection_id = _upload_collection(
                xnat, xnat_url, project_id, experiment_id, label, work_dir / "seg.dcm", args.overwrite
            )
            _log(f"    ✅ {experiment_id} ({slices} slices) → {collection_id}")
            return True

    _log(f"    ↷ {experiment_id}: no {args.label_prefix}*.nii.gz in any scan")

    return False


def process_xnat(xnat_url: str, args: argparse.Namespace) -> int:
    """Publish collections for every labelled session of one trust's XNAT.

    Returns:
        int: Number of sessions that got a collection.
    """
    xnat = requests.Session()
    xnat.auth = (args.xnat_username, args.xnat_password)
    project_id = find_project(xnat, xnat_url, args.flip_project_id)
    if not project_id:
        _log(f"  ↷ {xnat_url}: no project with secondary_ID={args.flip_project_id}")
        return 0

    _log(f"  🔎 {xnat_url}: project {project_id}")
    resp = xnat.get(f"{xnat_url}/data/projects/{project_id}/experiments?format=json", timeout=120)
    resp.raise_for_status()
    experiments = resp.json()["ResultSet"]["Result"]
    if args.limit:
        experiments = experiments[: args.limit]

    uploaded = 0
    for experiment in experiments:
        if process_experiment(xnat, xnat_url, project_id, experiment, args):
            uploaded += 1

    return uploaded


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--flip-project-id",
        required=True,
        help="FLIP project UUID; matched against each XNAT project's secondary_ID",
    )
    parser.add_argument(
        "--xnat-url",
        action="append",
        dest="xnat_urls",
        default=None,
        help=f"Trust XNAT base URL; repeatable (default: {' '.join(DEFAULT_XNAT_URLS)})",
    )
    parser.add_argument("--xnat-username", default=os.environ.get("XNAT_ADMIN_USER", "admin"))
    parser.add_argument("--xnat-password", default=os.environ.get("XNAT_ADMIN_PASSWORD", "admin"))
    parser.add_argument("--label-prefix", default="label_", help="Filename prefix of the NIfTI labels")
    parser.add_argument("--segment-label", default="Spleen", help="Anatomical label encoded in the SEG")
    parser.add_argument(
        "--collection-name",
        default="Spleen segmentation",
        help="SeriesDescription of the SEG — the name the viewer's Masks > Import dialog lists",
    )
    parser.add_argument("--collection-label", default="SEG", help="XNAT assessor label prefix")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N sessions per XNAT (0 = all)")
    parser.add_argument("--dcmqi-image", default=DCMQI_IMAGE)
    parser.add_argument("--overwrite", action="store_true", help="Replace collections that already exist")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv)
    urls = args.xnat_urls or list(DEFAULT_XNAT_URLS)
    _log(f"🎨 Publishing DICOM-SEG collections for FLIP project {args.flip_project_id}")
    total = 0
    for xnat_url in urls:
        try:
            total += process_xnat(xnat_url, args)
        except (requests.exceptions.RequestException, SegUploadError) as exc:
            _log(f"  ❌ {xnat_url}: {exc}")
            return 1

    _log(f"🎉 {total} session(s) now carry a DICOM-SEG collection")

    return 0


if __name__ == "__main__":
    sys.exit(main())
