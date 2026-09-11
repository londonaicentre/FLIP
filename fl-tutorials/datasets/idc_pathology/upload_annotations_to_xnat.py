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
"""Upload the IDC pathology reference annotations into a FLIP project's XNAT.

*Data enrichment* for the nuclei-detection tutorial: it puts each slide's DICOM Microscopy Bulk
Simple Annotation object beside the slide in XNAT, so the evaluation app finds a reference to score
against. Same role as ``spleen/upload_spleen_labels_to_xnat.py``, and built on the same
``flip.xnat.enrichment`` framework.

**Why enrichment rather than the imaging pull.** XNAT's DICOM receiver runs on dcm4che 2.0.29, whose
UID table has no entry for the annotation SOP class ``1.2.840.10008.5.1.4.1.1.91.1``. It offers no
presentation context for it, so a C-STORE of an annotation instance is refused outright::

    Cannot C-Store an instance of SOPClassUID 1.2.840.10008.5.1.4.1.1.91.1,
    the destination has not accepted any TransferSyntax for this SOPClassUID

Slide and annotation share one accession, so that refusal fails the whole study's C-MOVE — putting
annotations in Orthanc stops the *slides* arriving too, and the study wedges in ``ISSUED``, reported
as ``Processing`` forever (FLIP#662). ``seed_trusts.py`` therefore seeds slides only, and the
annotations travel this way instead: over XNAT's REST API, which negotiates no presentation contexts
and so carries any SOP class.

The annotation is written **into the slide scan's own ``DICOM`` resource**, not a resource of its
own. That is what makes the app need no special case: ``flip.get_by_accession_number(...,
ResourceType.DICOM)`` hands back that resource's contents, and ``data_utils`` picks the two objects
out of it by SOP Class rather than by filename.

Enrich every Trust in the project, not one — each Trust's XNAT holds only its own studies. Pass a
repeated ``--xnat-url`` (credentials from ``XNAT_USER``/``XNAT_PASS``) or a ``--credentials-file``
per Trust; the whole manifest goes to every server and each ignores the others' accessions.

Typical use, through the Makefile::

    make -C fl-tutorials upload-idc-pathology-annotations FLIP_PROJECT_ID=<uuid> \\
        XNAT_URLS="http://127.0.0.1:8105 http://127.0.0.1:8107" DRY_RUN=1
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

from flip.exceptions import XnatError
from flip.xnat import EnrichmentItem, XnatClient, run_enrichment

logger = logging.getLogger(__name__)

# The pinned selection these annotations belong to. Committed beside this script, because the
# imaging is fetched from IDC on demand and never re-hosted -- see build_omop_project.py.
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "idc_pathology"
# The published manifest (omop-csv/pathology_project/source/manifest.csv on aicentreflip/trust-data), as
# fetched into the data root by `make -C fl-tutorials fetch-idc-pathology-manifest`.
DEFAULT_MANIFEST = DEFAULT_DATA_DIR / "manifest.csv"

ANNOTATION_FILENAME = "annotation.dcm"

# Written into the slide scan's own DICOM resource; see the module docstring for why this rather
# than a resource of its own.
ANNOTATION_RESOURCE = "DICOM"


def annotation_path(data_dir: Path, site: str, accession_id: str) -> Path:
    """Return the local path of one accession's annotation object.

    Args:
        data_dir (Path): Root of the downloaded tree, e.g. ``fl-tutorials/data/idc_pathology``.
        site (str): The manifest's ``site`` column, which names the per-Trust subdirectory.
        accession_id (str): The accession the annotation belongs to.

    Returns:
        Path: Path to that accession's ``annotation.dcm``.
    """
    return data_dir / site / "accession-resources" / accession_id / ANNOTATION_FILENAME


def build_manifest(data_dir: Path, manifest_csv: Path, trust: str | None = None) -> list[EnrichmentItem]:
    """Build the upload manifest from the pinned selection.

    Built from the published manifest rather than by walking the download tree, so a partial
    download is reported as missing files naming the command that fetches them, instead of silently
    enriching a subset of the cohort -- which would leave some slides unscoreable while the run
    still looked clean.

    Args:
        data_dir (Path): Root of the downloaded tree.
        manifest_csv (Path): The published ``manifest.csv``, fetched into the data root.
        trust (str | None): Restrict to one Trust, given either as ``Trust_1`` or as ``1``. This is
            the dataset's own per-site partition, not the FL kit slot of the same name.

    Returns:
        list[EnrichmentItem]: One item per accession, targeting ``annotation.dcm``.

    Raises:
        FileNotFoundError: If the manifest is absent, or any annotation it names has not been
            downloaded. Names the command that fetches them.
        ValueError: If the manifest carries no rows for the requested Trust.
    """
    if not manifest_csv.is_file():
        raise FileNotFoundError(
            f"{manifest_csv} is missing — fetch the published manifest with "
            "`make -C fl-tutorials fetch-idc-pathology-manifest`."
        )

    wanted = f"Trust_{trust}" if trust and trust.isdigit() else trust

    with manifest_csv.open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if not wanted or row["site"] == wanted]

    if not rows:
        raise ValueError(f"{manifest_csv} has no rows for trust {wanted!r}.")

    items: list[EnrichmentItem] = []
    missing: list[Path] = []
    for row in rows:
        path = annotation_path(data_dir, row["site"], row["accession_id"])
        if not path.is_file():
            missing.append(path)
            continue
        items.append(
            EnrichmentItem(
                accession_id=row["accession_id"],
                file_path=path,
                # Explicit, so the name never depends on what the pull happened to leave in the
                # resource. The app matches on SOP Class, so this name is for humans reading XNAT.
                target_filename=ANNOTATION_FILENAME,
            )
        )

    if missing:
        listed = "\n".join(f"   {p}" for p in missing[:10])
        more = f"\n   ... and {len(missing) - 10} more" if len(missing) > 10 else ""
        raise FileNotFoundError(
            f"{len(missing)} annotation(s) named by {manifest_csv.name} are not downloaded:\n{listed}{more}\n"
            "   Run `make -C fl-tutorials download-idc-pathology-data` first."
        )

    logger.info("Manifest: %d annotation(s) from %s", len(items), manifest_csv.name)
    return items


def _build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        argparse.ArgumentParser: The configured parser.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--flip-project-id", required=True, help="FLIP Central Hub project id (a UUID).")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Root of the downloaded pathology tree (default: {DEFAULT_DATA_DIR}).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"Published manifest of the pinned selection, fetched into the data root (default: {DEFAULT_MANIFEST}).",
    )
    parser.add_argument(
        "--trust",
        metavar="N",
        help="Only upload accessions from this Trust's partition ('1' or 'Trust_1'). Usually "
        "unnecessary: without it the whole manifest goes to every server and each reports the "
        "others' accessions as 'no matching scan'.",
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
        "Credentials come from --xnat-user/--xnat-password.",
    )
    parser.add_argument("--xnat-user", default=os.environ.get("XNAT_USER"), help="Username for --xnat-url.")
    parser.add_argument("--xnat-password", default=os.environ.get("XNAT_PASS"), help="Password for --xnat-url.")
    parser.add_argument("--overwrite", action="store_true", help="Replace annotations already present.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and report, but upload nothing.")
    parser.add_argument(
        "--allow-no-op",
        action="store_true",
        help="Exit 0 even when no destination was resolved anywhere.",
    )
    parser.add_argument(
        "--require-full-coverage",
        action="store_true",
        help="Also fail unless every scan in the visited project(s) received its annotation.",
    )
    return parser


def build_clients(args: argparse.Namespace) -> list[XnatClient]:
    """Build one client per XNAT server named on the command line.

    Explicit flags win over the environment, and ``--xnat-url`` over ``--credentials-file``; with
    neither, the single-server ``XNAT_HOST``/``XNAT_USER``/``XNAT_PASS`` path is used.

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
    """Run the annotation upload.

    Args:
        argv (list[str] | None): Argument vector, defaulting to ``sys.argv[1:]``.

    Returns:
        int: Process exit code; 0 on success, 1 on any failure.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_parser().parse_args(argv)

    try:
        items = build_manifest(args.data_dir.resolve(), args.manifest.resolve(), args.trust)
    except (FileNotFoundError, ValueError) as err:
        print(f"❌ {err}", file=sys.stderr)
        return 1

    try:
        report = run_enrichment(
            build_clients(args),
            items,
            flip_project_id=args.flip_project_id,
            resource=ANNOTATION_RESOURCE,
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
