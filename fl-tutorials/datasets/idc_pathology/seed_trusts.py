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
"""Seed the pathology slides and their OMOP rows into running dev trusts.

The pathology tutorial normally runs under ``LOCAL_DEV``, reading slides straight off disk. To drive
it through the *platform* -- a cohort query that resolves, an imaging pull into XNAT, a viewer that
has something to show -- the same data has to exist where the platform looks for it: whole-slide
DICOM in each trust's Orthanc, and the matching OMOP rows in each trust's database.

This puts it there, additively, against an already-running stack. It is deliberately not folded into
``make update-orthanc-data`` / ``update-omop-data``: those replace a trust's whole data directory
from a prepared tarball, so they cannot add to a live one, and hosting ~1.4 GB of whole-slide imaging
in that shared archive is exactly what the tutorial's download-on-demand design avoids.

Both stores are seeded by one script because they have to agree. A slide in Orthanc with no OMOP row
is invisible to a cohort query; an OMOP row with no slide is a pull that stalls. Splitting them into
two commands would make disagreement the easy mistake.

**The OMOP ``source_trust`` column decides which trust gets what** -- not the ``Trust_N`` directory
names, which were generated from that same column. Reading the column keeps one source of truth, and
lets the agreement between the two be asserted rather than assumed.

Usage::

    python seed_trusts.py --trust 1=http://127.0.0.1:8042,postgresql://...@127.0.0.1:5434/trustomopdb \\
                          --trust 2=http://127.0.0.1:8044,postgresql://...@127.0.0.1:5436/trustomopdb
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg2
import requests

logger = logging.getLogger("seed_trusts")

REPO_ROOT = Path(__file__).resolve().parents[3]
OMOP_DIR = Path(__file__).resolve().parent / "omop" / "pathology_project"
DEFAULT_SLIDES_DIR = REPO_ROOT / "fl-tutorials" / "data" / "idc_pathology"

# Written in dependency order: person before the visit that references it, and so on. image_occurrence
# last, because it references all three.
OMOP_TABLES = ("person", "visit_occurrence", "procedure_occurrence", "image_occurrence")

# The column naming each row's trust. Present in every table this seeds.
TRUST_COLUMN = "source_trust"

TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class Trust:
    """One dev trust's two write targets."""

    number: str
    orthanc_url: str
    omop_dsn: str
    orthanc_auth: tuple[str, str] | None = None

    def __str__(self) -> str:
        return f"trust {self.number}"


def read_table(name: str) -> list[dict[str, str]]:
    path = OMOP_DIR / f"{name}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Run `make -C fl-tutorials build-idc-pathology-omop` to derive it."
        )
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def rows_for(table: list[dict[str, str]], trust_number: str) -> list[dict[str, str]]:
    return [row for row in table if row.get(TRUST_COLUMN) == trust_number]


def seed_omop(trust: Trust, tables: dict[str, list[dict[str, str]]], dry_run: bool) -> int:
    """Replace this trust's rows for the patients the pathology project owns.

    Delete-then-insert, scoped by ``person_source_value``, rather than an insert that skips
    conflicts. The surrogate ids look stable but are not: re-resolving the manifest renumbers every
    table from ID_BASE, so the same slide comes back under a new ``image_occurrence_id``. Skipping
    on primary-key conflict therefore does not deduplicate -- it silently doubles the cohort, which
    is how a 12-slide trust briefly became a 17-row one here.

    Scoping by the patient barcode instead means re-running converges on exactly what the CSVs say,
    whatever the ids did, and touches nothing outside this mock project.
    """
    # Report without connecting: a dry run is for checking what would happen, so it must not also
    # require working database credentials.
    if dry_run:
        for name in OMOP_TABLES:
            logger.info("  [dry-run] %s: would insert %d row(s)", name, len(rows_for(tables[name], trust.number)))
        return 0

    barcodes = [row["person_source_value"] for row in rows_for(tables["person"], trust.number)]
    inserted = 0
    with psycopg2.connect(trust.omop_dsn) as connection, connection.cursor() as cursor:
        # Clear first, in reverse dependency order so the foreign keys hold at every step.
        cursor.execute("SELECT person_id FROM omop.person WHERE person_source_value = ANY(%s);", (barcodes,))
        person_ids = [row[0] for row in cursor.fetchall()]
        if person_ids:
            for name in reversed(OMOP_TABLES[1:]):
                cursor.execute(f"DELETE FROM omop.{name} WHERE person_id = ANY(%s);", (person_ids,))
            cursor.execute("DELETE FROM omop.person WHERE person_id = ANY(%s);", (person_ids,))

        for name in OMOP_TABLES:
            rows = rows_for(tables[name], trust.number)
            if not rows:
                continue
            # source_trust is a partition marker for the mock data, not part of the OMOP CDM, so it
            # is dropped before the insert -- the trust's own database has no such column.
            columns = [c for c in rows[0] if c != TRUST_COLUMN]
            placeholders = ", ".join(["%s"] * len(columns))
            statement = f"INSERT INTO omop.{name} ({', '.join(columns)}) VALUES ({placeholders})"
            for row in rows:
                cursor.execute(statement, [row[c] or None for c in columns])
                inserted += cursor.rowcount
            logger.info("  %s: %d row(s)", name, len(rows))
    return inserted


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
    """Parse ``<number>=<orthanc-url>,<omop-dsn>[,<user>:<password>]``."""
    number, _, rest = value.partition("=")
    parts = [p for p in rest.split(",") if p]
    if not number.isdigit() or len(parts) < 2:
        raise argparse.ArgumentTypeError(
            f"--trust expects '<number>=<orthanc-url>,<omop-dsn>[,<user>:<password>]', got {value!r}"
        )
    auth: tuple[str, str] | None = None
    if len(parts) > 2:
        user, _, password = parts[2].partition(":")
        auth = (user, password)
    return Trust(number=number, orthanc_url=parts[0], omop_dsn=parts[1], orthanc_auth=auth)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--trust",
        action="append",
        required=True,
        type=parse_trust,
        metavar="N=ORTHANC_URL,OMOP_DSN[,USER:PASS]",
        help="A trust to seed. Repeat for each; the number must match the OMOP source_trust value.",
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

    tables = {name: read_table(name) for name in OMOP_TABLES}
    images = tables["image_occurrence"]

    seeded_any = False
    for trust in args.trust:
        accessions = sorted({row["accession_id"] for row in rows_for(images, trust.number)})
        if not accessions:
            logger.warning("%s: no rows with %s=%s -- skipping", trust, TRUST_COLUMN, trust.number)
            continue
        logger.info("%s: %d slide(s)", trust, len(accessions))
        prune_annotations(trust, accessions, args.dry_run)
        seed_orthanc(trust, accessions, args.slides_dir, args.dry_run)
        seed_omop(trust, tables, args.dry_run)
        seeded_any = True

    # A run that seeds nothing anywhere is almost always a wrong --trust number or an unbuilt CSV,
    # and it would otherwise surface much later as a cohort query that matches nothing. Fail here,
    # where the cause is still on screen -- the same reasoning as the spleen uploader's no-op guard.
    if not seeded_any:
        logger.error("Nothing was seeded: no trust matched a %s value in the OMOP data.", TRUST_COLUMN)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
