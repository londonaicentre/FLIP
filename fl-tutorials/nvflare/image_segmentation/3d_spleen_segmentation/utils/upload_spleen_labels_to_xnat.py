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
import os
import sys
from pathlib import Path

import requests
from flip.exceptions import XnatError
from flip.xnat import EnrichmentItem, XnatClient, run_enrichment

logger = logging.getLogger(__name__)

HF_TRUST_DATA_REPO = "aicentreflip/trust-data"

HF_TRUST_DATA_REVISION = os.environ.get("HF_TRUST_DATA_REVISION", "main")
"""Dataset revision to read the mapping at.

``main`` by default, overridable so a run can be pinned to a commit sha. This mirrors
``trust/omop-db/update_omop_data.sh`` and ``trust/orthanc/update_orthanc_data.sh``, which read the
same variable — the mock DICOMs, the pgdata and this mapping all come from that one dataset, so
pinning them together is the only way to pin them coherently.
"""

MAPPING_CACHE_FILENAME = ".accession_map.csv"
"""Cached mapping, written beside the labels directory so re-runs and offline runs work."""

# The OMOP CSV export is published per data version alongside the pgdata tars the trusts are
# seeded from, so the mapping is read at whatever version this checkout deploys — one pin, in
# trust/omop-db/.data_version, rather than a second copy that could silently drift from it.
# Note this pins the *path*, not the bytes: HF_TRUST_DATA_REVISION above pins the revision those
# bytes are read at, and defaults to a moving `main` like every other dataset read in this repo.
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


def _parse_accession_map(text: str, origin: str) -> dict[str, tuple[str, str]]:
    """Parse the OMOP ``image_occurrence`` export into the accession mapping.

    Args:
        text (str): The CSV text.
        origin (str): Where it came from, for error messages.

    Returns:
        dict[str, tuple[str, str]]: ``accession_id -> (msd_case, source_trust)``.

    Raises:
        SystemExit: If the CSV lacks the expected columns.
    """
    reader = csv.DictReader(text.splitlines())
    missing = {"accession_id", "local_path", "source_trust"} - set(reader.fieldnames or [])
    if missing:
        raise SystemExit(f"❌ {origin} is missing column(s): {', '.join(sorted(missing))}")

    return {
        row["accession_id"]: (Path(row["local_path"]).name, row["source_trust"])
        for row in reader
        if row.get("accession_id") and row.get("local_path")
    }


def fetch_accession_map(url: str | None = None, cache_dir: Path | None = None) -> dict[str, tuple[str, str]]:
    """Fetch the accession-to-MSD-case mapping from the public trust-data dataset.

    Cached on disk when ``cache_dir`` is given, so a re-run — and the second Trust of a two-Trust
    enrichment — needs no further egress to huggingface.co, and an offline run still works. The
    cache key is the URL, which already carries the data version and revision, so a version bump
    fetches afresh rather than serving a stale mapping.

    Args:
        url (str | None): URL of the OMOP ``image_occurrence.csv`` export; defaults to the export
            for the version in ``trust/omop-db/.data_version``.
        cache_dir (Path | None): Directory to cache the CSV in. No caching when ``None``.

    Returns:
        dict[str, tuple[str, str]]: ``accession_id -> (msd_case, source_trust)``, where ``msd_case``
        is e.g. ``spleen_2``.

    Raises:
        SystemExit: If the CSV cannot be fetched or lacks the expected columns.
    """
    url = url or mapping_csv_url()
    cache_path = cache_dir / MAPPING_CACHE_FILENAME if cache_dir else None

    if cache_path is not None and cache_path.is_file():
        cached = cache_path.read_text()
        header, _, body = cached.partition("\n")
        if header == f"# {url}":
            logger.info(f"📄 Using cached accession mapping from {cache_path}")
            mapping = _parse_accession_map(body, str(cache_path))
            logger.info(f"   {len(mapping)} accession(s) in the mapping")
            return mapping

    logger.info(f"⬇️  Fetching accession mapping from {url}")
    try:
        response = requests.get(url, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as err:
        raise SystemExit(f"❌ Could not fetch the accession mapping: {err}")

    mapping = _parse_accession_map(response.text, url)
    if cache_path is not None:
        try:
            cache_path.write_text(f"# {url}\n{response.text}")
        except OSError as err:
            # A read-only labels directory is not a reason to fail an upload.
            logger.warning(f"   could not cache the mapping at {cache_path}: {err}")

    logger.info(f"   {len(mapping)} accession(s) in the mapping")
    return mapping


INCOMPLETE_DOWNLOAD_HELP = """   The two spleen downloads differ, so check the one --labels-dir points at:
     NVFLARE (data/spleen/images)              make -C fl-tutorials/nvflare download-spleen-data NUM_CASES=41
     Flower  (data/spleen/accession-resources) the HF snapshot ships a fixed 6-case subset, and
                                               ignores NUM_CASES — point --labels-dir at the
                                               NVFLARE download for full coverage."""
"""Remediation text naming both backends.

Printed unconditionally rather than inferred from ``--labels-dir``: guessing wrong sends the reader
to a command that cannot help, and ``NUM_CASES`` genuinely does nothing on the Flower path.
"""


def build_manifest(labels_dir: Path, trust: str | None = None) -> list[EnrichmentItem]:
    """Pair each mapped accession with its local MSD label file.

    Args:
        labels_dir (Path): Directory of ``subject_N/scans/label_spleen_N.nii.gz`` trees, as produced
            by ``download_spleen_dataset.py`` (NVFLARE) or the Flower spleen download.
        trust (str | None): Keep only accessions whose ``source_trust`` matches, e.g. ``"1"``.

    Returns:
        list[EnrichmentItem]: Items whose label file exists on disk.

    Raises:
        SystemExit: If ``labels_dir`` does not exist, ``trust`` names no Trust in the mapping, or
            no usable pairs are found.
    """
    if not labels_dir.is_dir():
        raise SystemExit(f"❌ Labels directory not found: {labels_dir}\n{INCOMPLETE_DOWNLOAD_HELP}")

    mapping = fetch_accession_map(cache_dir=labels_dir)

    # Validated against the data rather than pinned in `choices`, so a mapping with more Trusts
    # works and a typo is answered with the Trusts that actually exist.
    known_trusts = sorted({source_trust for _, source_trust in mapping.values()})
    if trust is not None and trust not in known_trusts:
        raise SystemExit(f"❌ --trust {trust} is not in the mapping. Trusts present: {', '.join(known_trusts)}")

    items: list[EnrichmentItem] = []
    missing_cases: list[str] = []
    for accession_id, (msd_case, source_trust) in sorted(mapping.items()):
        if trust is not None and source_trust != trust:
            continue

        # download_spleen_dataset.py rewrites MSD's "spleen_N" as the subject folder "subject_N",
        # while the file inside keeps the MSD case name.
        label_path = labels_dir / msd_case.replace("spleen_", "subject_") / "scans" / f"label_{msd_case}.nii.gz"
        if not label_path.is_file():
            missing_cases.append(msd_case)
            continue
        items.append(EnrichmentItem(accession_id=accession_id, file_path=label_path))

    if not items:
        raise SystemExit(
            f"❌ No label files found under {labels_dir}.\n"
            f"   Expected e.g. {labels_dir}/subject_2/scans/label_spleen_2.nii.gz\n"
            f"{INCOMPLETE_DOWNLOAD_HELP}"
        )

    # State coverage against the post-filter total. With the default NUM_CASES=10 a partial run is
    # the norm, not the exception, and it ends in a silently truncated training set — so name the
    # fraction and the cases rather than leaving the reader to work out the denominator.
    expected = len(items) + len(missing_cases)
    if missing_cases:
        overflow = f", … (+{len(missing_cases) - 5} more)" if len(missing_cases) > 5 else ""
        shown = ", ".join(missing_cases[:5]) + overflow
        logger.warning(
            f"⚠️  Only {len(items)}/{expected} mapped case(s) present in {labels_dir} — "
            f"training will see a truncated dataset.\n   Missing: {shown}\n{INCOMPLETE_DOWNLOAD_HELP}"
        )
    else:
        logger.info(f"   {len(items)}/{expected} mapped case(s) present locally")

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
        metavar="N",
        help="Only upload accessions whose OMOP source_trust column is N (dev roster: 1=GSTT, 2=KCH). "
        "This is the OMOP data partition, NOT the FL kit slot of the same name, which the hub "
        "assigns at registration. Usually unnecessary: without it the whole mapping goes to every "
        "server and each reports the others' studies as 'no matching scan'.",
    )
    parser.add_argument(
        "--credentials-file",
        action="append",
        dest="credentials_files",
        metavar="PATH",
        help='JSON file of {"server": ..., "user": ..., "password": ...}. Repeat once per Trust.',
    )
    parser.add_argument(
        "--xnat-url",
        action="append",
        dest="xnat_urls",
        metavar="URL",
        help="XNAT base URL, repeatable — one per Trust, enriching the whole roster in one run. "
        "Credentials come from --xnat-user/--xnat-password. Simpler than a credentials file when "
        "every Trust shares a login, as on the dev stack.",
    )
    parser.add_argument("--xnat-user", default=os.environ.get("XNAT_USER"), help="Username for --xnat-url.")
    parser.add_argument("--xnat-password", default=os.environ.get("XNAT_PASS"), help="Password for --xnat-url.")
    parser.add_argument("--overwrite", action="store_true", help="Replace labels that are already present.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and report, but upload nothing.")
    parser.add_argument(
        "--allow-no-op",
        action="store_true",
        help="Exit 0 even when no destination was resolved anywhere.",
    )
    parser.add_argument(
        "--require-full-coverage",
        action="store_true",
        help="Also fail unless every scan in the visited project(s) received its label.",
    )
    return parser


def build_clients(args: argparse.Namespace) -> list[XnatClient]:
    """Build one client per XNAT server named on the command line.

    Explicit flags win over the environment, and ``--xnat-url`` over ``--credentials-file``; with
    neither, the single-server ``XNAT_HOST``/``XNAT_USER``/``XNAT_PASS`` path is unchanged.

    Args:
        args (argparse.Namespace): Parsed arguments.

    Returns:
        list[XnatClient]: One client per server.

    Raises:
        XnatError: If ``--xnat-url`` is given without a username and password.
    """
    if args.xnat_urls:
        if not args.xnat_user or not args.xnat_password:
            raise XnatError("--xnat-url needs --xnat-user and --xnat-password (or XNAT_USER / XNAT_PASS)")
        return [XnatClient(server=url, user=args.xnat_user, password=args.xnat_password) for url in args.xnat_urls]
    if args.credentials_files:
        return [XnatClient.from_config_file(path) for path in args.credentials_files]
    return [XnatClient.from_env()]


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
        report = run_enrichment(
            build_clients(args),
            items,
            flip_project_id=args.flip_project_id,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
    except XnatError as err:
        print(f"❌ {err}", file=sys.stderr)
        return 1

    print(report.render())
    return report.exit_code(allow_no_op=args.allow_no_op, require_full_coverage=args.require_full_coverage)


if __name__ == "__main__":
    raise SystemExit(main())
