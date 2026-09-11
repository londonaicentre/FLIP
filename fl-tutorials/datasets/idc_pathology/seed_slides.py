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
"""Seed the pathology slides into running dev trusts' Orthanc, one trust per manifest site.

The pathology tutorial normally runs under ``LOCAL_DEV``, reading slides straight off disk. To drive
it through the *platform* -- a cohort query that resolves, an imaging pull into XNAT, a viewer that
has something to show -- the same data has to exist where the platform looks for it: whole-slide
DICOM in each trust's Orthanc, and the matching OMOP rows in each trust's database.

This is the Orthanc half. The OMOP half is the platform's own seed pipeline
(``make -C trust seed-omop KIT=<CODE> PROJECTS=pathology_project``), which fetches the published
``omop-csv/pathology_project/`` tables at the pinned data version and loads this trust's
``source_trust`` slice; ``make -C fl-tutorials seed-idc-pathology`` runs both halves. The seed
pipeline's DICOM half (``seed-orthanc``) is deliberately *not* used: it expects
``dicom/<project>.tar.gz`` on the dataset, and re-hosting gigabytes of a public archive is exactly what
this tutorial's download-on-demand design avoids. The slides are posted here from the local IDC
download instead.

The two halves still agree by construction: both are keyed on the same published manifest at the same
tag. The OMOP tables are derived from it (``build_omop_project.py``, gated by
``utils/verify_omop_tables.py``), and this script reads its ``site`` column to decide which trust
receives which slide -- ``Trust_N`` is the manifest's name for OMOP ``source_trust`` N. A slide in
Orthanc with no OMOP row is invisible to a cohort query; an OMOP row with no slide is a pull that
stalls; sharing one input rules both out.

Usage::

    python seed_slides.py --trust 1=http://127.0.0.1:8042,orthanc:orthanc \\
                          --trust 2=http://127.0.0.1:8044,orthanc:orthanc
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("seed_slides")

FL_TUTORIALS_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SLIDES_DIR = FL_TUTORIALS_DIR / "data" / "idc_pathology"
DEFAULT_MANIFEST = DEFAULT_SLIDES_DIR / "manifest.csv"

# The manifest column naming each slide's site, ``Trust_<N>`` -- N is the OMOP source_trust the
# published tables carry for that slide, so the same number selects the trust's rows on both sides.
SITE_COLUMN = "site"

TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class Trust:
    """One dev trust's Orthanc."""

    number: str
    orthanc_url: str
    orthanc_auth: tuple[str, str] | None = None

    def __str__(self) -> str:
        return f"trust {self.number}"


def read_manifest(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Fetch the published manifest first: "
            "`make -C fl-tutorials fetch-idc-pathology-manifest`."
        )
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def trust_number_of(site: str) -> str:
    """The OMOP ``source_trust`` a manifest site label names: ``Trust_2`` is ``"2"``."""
    digits = "".join(character for character in site if character.isdigit())
    if not digits:
        raise ValueError(f"Cannot derive a trust number from site {site!r}; expected a name like 'Trust_1'.")
    return str(int(digits))


def accessions_for(manifest: list[dict[str, str]], trust_number: str) -> list[str]:
    """Every accession the manifest assigns to one trust, sorted, each exactly once."""
    return sorted({row["accession_id"] for row in manifest if trust_number_of(row[SITE_COLUMN]) == trust_number})


# Only the slide is seeded into Orthanc. The reference annotations reach XNAT by data enrichment
# instead -- `upload_annotations_to_xnat.py`, the same route the spleen tutorial uses for its NIfTI
# labels -- and this is a correctness requirement, not a preference.
#
# XNAT's DICOM receiver runs on dcm4che 2.0.29, whose UID table has no entry for the Microscopy Bulk
# Simple Annotations SOP class (1.2.840.10008.5.1.4.1.1.91.1). It therefore offers no presentation
# context for it, and Orthanc's C-STORE is refused:
#
#     Cannot C-Store an instance of SOPClassUID 1.2.840.10008.5.1.4.1.1.91.1,
#     the destination has not accepted any TransferSyntax for this SOPClassUID
#
# Because slide and annotation share one accession, that refusal fails the *whole study's* C-MOVE, so
# seeding the annotation here does not merely fail to deliver it -- it stops the slide arriving too.
# The study then wedges in ISSUED and is reported as `Processing` forever (FLIP#662), which is
# indistinguishable from a slow whole-slide transfer. An earlier version of this file seeded both and
# asserted "one pull brings both into XNAT"; that was never true on a real trust.
#
# The whole-slide SOP class (1.2.840.10008.5.1.4.1.1.77.1.6) *is* in dcm4che 2's table, so the slide
# transfers normally once the annotation is out of the way.
ORTHANC_FILES = ("slide.dcm",)


def accession_dir(slides_dir: Path, trust_number: str, accession: str) -> Path:
    return slides_dir / f"Trust_{trust_number}" / "accession-resources" / accession


def slide_path(slides_dir: Path, trust_number: str, accession: str) -> Path:
    return accession_dir(slides_dir, trust_number, accession) / "slide.dcm"


def seed_orthanc(trust: Trust, accessions: list[str], slides_dir: Path, dry_run: bool) -> int:
    """POST each slide to this trust's Orthanc.

    Re-posting an instance Orthanc already holds returns the existing id rather than storing a second
    copy, so this is idempotent without a prior existence check.
    """
    posted = 0
    for accession in accessions:
        for filename in ORTHANC_FILES:
            path = accession_dir(slides_dir, trust.number, accession) / filename
            if not path.exists():
                raise FileNotFoundError(
                    f"{path} is missing. Run `make -C fl-tutorials download-idc-pathology-data` first."
                )
            if dry_run:
                logger.info("  [dry-run] would post %s/%s (%.0f MB)", accession, filename, path.stat().st_size / 1e6)
                continue
            with path.open("rb") as handle:
                response = requests.post(
                    f"{trust.orthanc_url.rstrip('/')}/instances",
                    data=handle,
                    headers={"Content-Type": "application/dicom"},
                    auth=trust.orthanc_auth,
                    timeout=TIMEOUT_SECONDS,
                )
            response.raise_for_status()
            body: dict[str, Any] = response.json()
            logger.info("  %s/%s -> %s", accession, filename, body.get("Status", "?"))
            posted += 1
    return posted


# SOP class of the annotation objects, and the Modality they carry in Orthanc. Named here because
# an Orthanc seeded by an earlier version of this script still holds them, and their mere presence
# under a shared accession fails the slide's C-MOVE (see ORTHANC_FILES above).
ANNOTATION_MODALITY = "ANN"


def prune_annotations(trust: Trust, accessions: list[str], dry_run: bool) -> int:
    """Delete any annotation series this script previously seeded into ``trust``'s Orthanc.

    Seeding is otherwise additive, so changing ``ORTHANC_FILES`` is not enough on a store that an
    earlier version already populated: the annotations would stay, and keep failing every slide's
    C-MOVE. Removing them is scoped to the accessions this project manages and to ``ANN`` series
    only, so nothing else in a shared dev PACS is touched.

    Args:
        trust (Trust): The trust whose Orthanc to prune.
        accessions (list[str]): Accessions this project manages.
        dry_run (bool): Report what would be deleted, delete nothing.

    Returns:
        int: Number of series deleted (or that would be, on a dry run).
    """
    base = trust.orthanc_url.rstrip("/")
    deleted = 0
    for accession in accessions:
        response = requests.post(
            f"{base}/tools/find",
            json={
                "Level": "Series",
                "Query": {"AccessionNumber": accession, "Modality": ANNOTATION_MODALITY},
                "Expand": False,
            },
            auth=trust.orthanc_auth,
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        for series_id in response.json():
            if dry_run:
                logger.info("  [dry-run] would delete %s series %s (%s)", ANNOTATION_MODALITY, series_id, accession)
                deleted += 1
                continue
            delete = requests.delete(f"{base}/series/{series_id}", auth=trust.orthanc_auth, timeout=TIMEOUT_SECONDS)
            delete.raise_for_status()
            logger.info("  pruned %s series for %s", ANNOTATION_MODALITY, accession)
            deleted += 1
    if deleted:
        logger.info(
            "  %s: removed %d annotation series -- they are delivered by "
            "`make -C fl-tutorials upload-idc-pathology-annotations` instead",
            trust,
            deleted,
        )
    return deleted


def parse_trust(value: str) -> Trust:
    """Parse ``<number>=<orthanc-url>[,<user>:<password>]``."""
    number, _, rest = value.partition("=")
    parts = [p for p in rest.split(",") if p]
    if not number.isdigit() or not parts:
        raise argparse.ArgumentTypeError(f"--trust expects '<number>=<orthanc-url>[,<user>:<password>]', got {value!r}")
    auth: tuple[str, str] | None = None
    if len(parts) > 1:
        user, _, password = parts[1].partition(":")
        auth = (user, password)
    return Trust(number=number, orthanc_url=parts[0], orthanc_auth=auth)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--trust",
        action="append",
        required=True,
        type=parse_trust,
        metavar="N=ORTHANC_URL[,USER:PASS]",
        help="A trust to seed. Repeat for each; the number must match the manifest's Trust_N site (OMOP source_trust).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"The published manifest, as fetched by make fetch-idc-pathology-manifest (default: {DEFAULT_MANIFEST}).",
    )
    parser.add_argument(
        "--slides-dir",
        type=Path,
        default=DEFAULT_SLIDES_DIR,
        help=f"Root of the downloaded IDC slides (default: {DEFAULT_SLIDES_DIR}).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would be seeded, write nothing.")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)

    manifest = read_manifest(args.manifest)

    seeded_any = False
    for trust in args.trust:
        accessions = accessions_for(manifest, trust.number)
        if not accessions:
            logger.warning("%s: no manifest rows for site Trust_%s -- skipping", trust, trust.number)
            continue
        logger.info("%s: %d slide(s)", trust, len(accessions))
        prune_annotations(trust, accessions, args.dry_run)
        seed_orthanc(trust, accessions, args.slides_dir, args.dry_run)
        seeded_any = True

    # A run that seeds nothing anywhere is almost always a wrong --trust number or the wrong manifest,
    # and it would otherwise surface much later as a pull that finds nothing to move. Fail here,
    # where the cause is still on screen -- the same reasoning as the spleen uploader's no-op guard.
    if not seeded_any:
        logger.error("Nothing was seeded: no trust matched a %s value in the manifest.", SITE_COLUMN)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
