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

"""Upload MSD spleen segmentation labels into a FLIP project's XNAT (the data-enrichment step).

The trust PACS supplies CT *images* only, but the spleen apps train on image/label pairs. This
script closes that gap for the tutorial: it pairs each label from the MSD download with the XNAT
scan holding the matching image, and uploads it as a ``label_*.nii.gz`` sibling.

**The mapping problem.** XNAT experiments are labelled by accession number, while MSD labels are
named by case (``label_spleen_2.nii.gz``). The mock DICOMs were generated from MSD but carry fully
synthetic identity, so nothing in the imaging data links a scan back to its MSD case. The join is
published alongside the mock data itself, in the trust-data OMOP export for the same data version
this checkout deploys (``trust/omop-db/.data_version``): ``image_occurrence.csv`` carries
``accession_id`` next to a ``local_path`` ending in the MSD case name, plus a ``source_trust``
column saying which Trust holds each study.

Run this **after** the image pull and **after** DICOM-to-NIfTI conversion — the target filename is
derived from the converted image, so running early skips every scan.

This is backend-agnostic: enrichment happens once per FLIP project, in XNAT, and both the NVFLARE
and Flower spleen tutorials read the result. Point ``--labels-dir`` at whichever backend's spleen
download you have.
"""

import argparse
import csv
import logging
import sys
from pathlib import Path

import requests
from flip.exceptions import XnatError
from flip.xnat import EnrichmentItem, XnatClient, upload_enrichment_files

logger = logging.getLogger(__name__)

HF_TRUST_DATA_REPO = "aicentreflip/trust-data"
HF_TRUST_DATA_REVISION = "main"

# The OMOP CSV export is published per data version alongside the pgdata tars the trusts are
# seeded from, so the mapping is read at whatever version this checkout deploys — one pin, in
# trust/omop-db/.data_version, rather than a second copy that could silently drift from it.
OMOP_DATA_VERSION_FILE = Path(__file__).resolve().parents[5] / "trust" / "omop-db" / ".data_version"

DOWNLOAD_TIMEOUT_SECONDS = 60


def omop_data_version() -> str:
    """Read the OMOP mock-data version this checkout deploys.

    Returns:
        str: The version string, e.g. ``20260729``.

    Raises:
        SystemExit: If the version file is missing or empty — better to stop than to guess a
            version and silently fetch a mapping for different mock data.
    """
    try:
        version = OMOP_DATA_VERSION_FILE.read_text().strip()
    except OSError as err:
        raise SystemExit(f"❌ Could not read {OMOP_DATA_VERSION_FILE}: {err}")
    if not version:
        raise SystemExit(f"❌ {OMOP_DATA_VERSION_FILE} is empty")
    return version


def mapping_csv_url(version: str | None = None) -> str:
    """Build the URL of the OMOP image_occurrence export carrying the accession mapping.

    Args:
        version (str | None): Data version; defaults to :func:`omop_data_version`.

    Returns:
        str: The download URL.
    """
    return (
        f"https://huggingface.co/datasets/{HF_TRUST_DATA_REPO}/resolve/{HF_TRUST_DATA_REVISION}/"
        f"omop-csv/{version or omop_data_version()}/spleen_project/image_occurrence.csv"
    )


def fetch_accession_map(url: str | None = None) -> dict[str, tuple[str, str]]:
    """Fetch the accession-to-MSD-case mapping from the public trust-data dataset.

    Args:
        url (str | None): URL of the OMOP ``image_occurrence.csv`` export; defaults to the export
            for the version in ``trust/omop-db/.data_version``.

    Returns:
        dict[str, tuple[str, str]]: ``accession_id -> (msd_case, source_trust)``, where ``msd_case``
        is e.g. ``spleen_2``.

    Raises:
        SystemExit: If the CSV cannot be fetched or lacks the expected columns.
    """
    url = url or mapping_csv_url()
    logger.info(f"⬇️  Fetching accession mapping from {url}")
    try:
        response = requests.get(url, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as err:
        raise SystemExit(f"❌ Could not fetch the accession mapping: {err}")

    reader = csv.DictReader(response.text.splitlines())
    required = {"accession_id", "local_path", "source_trust"}
    missing = required - set(reader.fieldnames or [])
    if missing:
        raise SystemExit(f"❌ {url} is missing column(s): {', '.join(sorted(missing))}")

    mapping = {
        row["accession_id"]: (Path(row["local_path"]).name, row["source_trust"])
        for row in reader
        if row.get("accession_id") and row.get("local_path")
    }
    logger.info(f"   {len(mapping)} accession(s) in the mapping")
    return mapping


def build_manifest(labels_dir: Path, trust: str | None = None) -> list[EnrichmentItem]:
    """Pair each mapped accession with its local MSD label file.

    Args:
        labels_dir (Path): Directory of ``subject_N/scans/label_spleen_N.nii.gz`` trees, as produced
            by ``download_spleen_dataset.py`` (NVFLARE) or the Flower spleen download.
        trust (str | None): Keep only accessions whose ``source_trust`` matches, e.g. ``"1"``.

    Returns:
        list[EnrichmentItem]: Items whose label file exists on disk.

    Raises:
        SystemExit: If ``labels_dir`` does not exist or yields no usable pairs.
    """
    if not labels_dir.is_dir():
        raise SystemExit(
            f"❌ Labels directory not found: {labels_dir}\n"
            f"   Run: make -C fl-tutorials download-spleen-data NUM_CASES=41"
        )

    items: list[EnrichmentItem] = []
    absent = 0
    for accession_id, (msd_case, source_trust) in sorted(fetch_accession_map().items()):
        if trust is not None and source_trust != trust:
            continue

        # download_spleen_dataset.py rewrites MSD's "spleen_N" as the subject folder "subject_N",
        # while the file inside keeps the MSD case name.
        label_path = labels_dir / msd_case.replace("spleen_", "subject_") / "scans" / f"label_{msd_case}.nii.gz"
        if not label_path.is_file():
            absent += 1
            continue
        items.append(EnrichmentItem(accession_id=accession_id, file_path=label_path))

    if absent:
        logger.info(f"   {absent} mapped case(s) not present in {labels_dir} (expected unless NUM_CASES=41)")
    if not items:
        raise SystemExit(
            f"❌ No label files found under {labels_dir}.\n"
            f"   Expected e.g. {labels_dir}/subject_2/scans/label_spleen_2.nii.gz\n"
            f"   Run: make -C fl-tutorials download-spleen-data NUM_CASES=41"
        )

    logger.info(f"   {len(items)} label file(s) ready to upload")
    return items


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        argparse.ArgumentParser: The configured parser.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--flip-project-id", required=True, help="FLIP Central Hub project id (a UUID).")
    parser.add_argument(
        "--labels-dir",
        type=Path,
        required=True,
        help="Directory of subject_N/scans/label_spleen_N.nii.gz trees from the spleen download.",
    )
    parser.add_argument(
        "--trust",
        choices=("1", "2"),
        help="Only upload accessions held by this Trust. Without it, studies belonging to the other "
        "Trust are reported as 'no matching scan', which is expected.",
    )
    parser.add_argument(
        "--credentials-file",
        help='JSON file of {"server": ..., "user": ..., "password": ...}. '
        "Defaults to the XNAT_HOST / XNAT_USER / XNAT_PASS environment variables.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace labels that are already present.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and report, but upload nothing.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the spleen label upload.

    Args:
        argv (list[str] | None): Argument vector, defaulting to ``sys.argv[1:]``.

    Returns:
        int: Process exit code; 0 on success, 1 on any failure.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_parser().parse_args(argv)

    items = build_manifest(args.labels_dir.resolve(), args.trust)

    try:
        client = XnatClient.from_config_file(args.credentials_file) if args.credentials_file else XnatClient.from_env()
        project_id = client.resolve_project_by_flip_project_id(args.flip_project_id)
        summary = upload_enrichment_files(
            client,
            project_id,
            items,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
    except XnatError as err:
        print(f"❌ {err}", file=sys.stderr)
        return 1

    print(f"\nXNAT project {project_id} ({client.server}):")
    print(summary.render())
    return 0 if summary.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
