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
    """Insert this trust's rows, skipping any already present.

    ON CONFLICT DO NOTHING rather than an upsert: the ids come from the committed CSVs and never
    change, so a row that is already there is the same row. That makes a re-run a no-op instead of a
    duplicate-key failure, which matters because seeding is a documented prerequisite people will run
    more than once.
    """
    # Report without connecting: a dry run is for checking what would happen, so it must not also
    # require working database credentials.
    if dry_run:
        for name in OMOP_TABLES:
            logger.info("  [dry-run] %s: would insert %d row(s)", name, len(rows_for(tables[name], trust.number)))
        return 0

    inserted = 0
    with psycopg2.connect(trust.omop_dsn) as connection, connection.cursor() as cursor:
        for name in OMOP_TABLES:
            rows = rows_for(tables[name], trust.number)
            if not rows:
                continue
            # source_trust is a partition marker for the mock data, not part of the OMOP CDM, so it
            # is dropped before the insert -- the trust's own database has no such column.
            columns = [c for c in rows[0] if c != TRUST_COLUMN]
            placeholders = ", ".join(["%s"] * len(columns))
            statement = (
                f"INSERT INTO omop.{name} ({', '.join(columns)}) "
                f"VALUES ({placeholders}) ON CONFLICT DO NOTHING"
            )
            for row in rows:
                cursor.execute(statement, [row[c] or None for c in columns])
                inserted += cursor.rowcount
            logger.info("  %s: %d row(s) present", name, len(rows))
    return inserted


# Both objects are seeded: the slide the detector reads, and the reference annotations it is scored
# against. They share an accession and a study, so one pull brings both into XNAT -- but only if both
# are in Orthanc to begin with. Seeding just the slide produces a run that pulls, converts and then
# fails at scoring with nothing to compare against.
ACCESSION_FILES = ("slide.dcm", "annotation.dcm")


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
        for filename in ACCESSION_FILES:
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
