#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["requests>=2.32.5"]
# ///
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
"""Seed a running trust Orthanc with this trust's slice of each project's DICOMs (FLIP#1100).

The DICOM twin of ``omop_db_tools.import_tables``: both read the same published OMOP tables and both
select this trust's rows by ``source_trust``, so the studies this puts into a trust's PACS are exactly
the ones its OMOP ``image_occurrence`` rows point at — by construction, not by keeping two snapshots
in lockstep by hand.

Per project, at one revision of the dataset (the data-version tag pinned in ``trust/.data_version``
unless ``--revision`` / ``HF_TRUST_DATA_REVISION`` says otherwise)::

    omop-csv/<project>/image_occurrence.csv  →  accession_id WHERE source_trust == <trust>
    dicom/<project>.tar.gz                   →  <accession>/*.dcm, streamed once into a cache
    each matching <accession>/*.dcm          →  POST /instances on this trust's Orthanc

Idempotent by construction: Orthanc dedupes on SOPInstanceUID and answers ``AlreadyStored`` for a
re-POST, so a re-run uploads nothing and changes nothing. Refuses to upload anything at all if any
accession in this trust's OMOP slice has no directory in the archive — that mismatch is the exact
failure this pipeline exists to prevent, and it must be loud.

Normally reached through ``make -C trust seed-orthanc KIT=<CODE>``, which supplies the slot number,
the PACS port and the credentials from the kit. Direct use::

    ORTHANC_USERNAME=… ORTHANC_PASSWORD=… uv run trust/orthanc/seed_orthanc.py \\
        --trust-index 2 --orthanc-url http://127.0.0.1:8044 --projects spleen_project cxr_project
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tarfile
import time
from collections import Counter
from pathlib import Path

import requests

HF_TRUST_DATA_REPO = os.environ.get("HF_TRUST_DATA_REPO", "aicentreflip/trust-data")

DEFAULT_PROJECTS = ["cxr_project", "spleen_project"]
# The one pin for the whole trust dataset — a git tag on the HF dataset. The OMOP side reads the
# same file, so the CSVs and the DICOM set are always the same version.
DATA_VERSION_FILE = Path(__file__).resolve().parents[1] / ".data_version"
COMPLETE_MARKER = ".complete"


def hf_url(revision: str, path: str, repo: str = HF_TRUST_DATA_REPO) -> str:
    """URL of one dataset file at a revision. The version is the revision, never part of the path."""
    return f"https://huggingface.co/datasets/{repo}/resolve/{revision}/{path}"


def resolve_revision(explicit: str | None) -> str:
    """The revision to fetch at: ``--revision``, else ``HF_TRUST_DATA_REVISION``, else the pinned tag."""
    return explicit or os.environ.get("HF_TRUST_DATA_REVISION") or DATA_VERSION_FILE.read_text().strip()


def select_accessions(rows: list[dict[str, str]], trust_index: int) -> list[str]:
    """This trust's accession numbers from a published ``image_occurrence`` table.

    Args:
        rows (list[dict[str, str]]): The CSV rows, each carrying ``accession_id`` and ``source_trust``.
        trust_index (int): 1-based trust index, compared against ``source_trust``.

    Returns:
        list[str]: Accession ids, in table order, duplicates removed.
    """
    seen: dict[str, None] = {}
    for row in rows:
        if int(row["source_trust"]) == trust_index:
            seen.setdefault(row["accession_id"], None)
    return list(seen)


def missing_accessions(project_dir: Path, accessions: list[str]) -> list[str]:
    """Accessions in this trust's OMOP slice with no DICOM directory in the archive."""
    return [a for a in accessions if not (project_dir / a).is_dir()]


def fetch_image_occurrence(revision: str, project: str, cache_dir: Path) -> list[dict[str, str]]:
    """The published ``image_occurrence.csv`` for a project at ``revision``, cached beside the DICOMs."""
    cache = cache_dir / revision / project / "image_occurrence.csv"
    if not cache.is_file():
        url = hf_url(revision, f"omop-csv/{project}/image_occurrence.csv")
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(response.content)
    with cache.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def ensure_dicoms(revision: str, project: str, cache_dir: Path) -> Path:
    """Stream ``dicom/<project>.tar.gz`` at ``revision`` into the cache once; return the project directory.

    The cache is keyed by revision (``<cache_dir>/<revision>/<project>/``), so a bump never reuses
    the previous version's instances. Extracts as it downloads (no 2× disk, no waiting for the whole
    archive), and marks completion with a file so a run interrupted mid-extract is redone rather
    than trusted.
    """
    project_dir = cache_dir / revision / project
    if (project_dir / COMPLETE_MARKER).is_file():
        return project_dir
    url = hf_url(revision, f"dicom/{project}.tar.gz")
    print(f"📦 {project}: streaming {url} into {project_dir}", flush=True)
    project_dir.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=300) as response:
        response.raise_for_status()
        with tarfile.open(fileobj=response.raw, mode="r|gz") as tar:
            tar.extractall(project_dir, filter="data")
    (project_dir / COMPLETE_MARKER).write_text(f"{url}\n")
    return project_dir


def upload_instance(session: requests.Session, orthanc_url: str, data: bytes, attempts: int = 3) -> str:
    """POST one instance; return Orthanc's ``Status`` (``Success`` or ``AlreadyStored``).

    Retries on connection errors and 5xx — a trust Orthanc under a bulk load can hiccup — but a
    4xx is a real rejection and is raised at once.
    """
    for attempt in range(1, attempts + 1):
        try:
            response = session.post(
                f"{orthanc_url}/instances", data=data, headers={"Content-Type": "application/dicom"}, timeout=120
            )
        except requests.ConnectionError as error:
            if attempt == attempts:
                raise
            print(f"   retry {attempt}/{attempts} after {error.__class__.__name__}", flush=True)
            time.sleep(attempt)
            continue
        if response.status_code >= 500 and attempt < attempts:
            print(f"   retry {attempt}/{attempts} after HTTP {response.status_code}", flush=True)
            time.sleep(attempt)
            continue
        response.raise_for_status()
        return str(response.json().get("Status", "?"))
    raise AssertionError("unreachable")


def delete_studies(session: requests.Session, orthanc_url: str, accessions: list[str]) -> int:
    """Delete every study in Orthanc whose AccessionNumber is one of ``accessions``."""
    deleted = 0
    for accession in accessions:
        found = session.post(
            f"{orthanc_url}/tools/find",
            json={"Level": "Study", "Query": {"AccessionNumber": accession}},
            timeout=60,
        )
        found.raise_for_status()
        for study_id in found.json():
            session.delete(f"{orthanc_url}/studies/{study_id}", timeout=120).raise_for_status()
            deleted += 1
    return deleted


def seed_project(
    session: requests.Session,
    orthanc_url: str,
    revision: str,
    project: str,
    trust_index: int,
    cache_dir: Path,
    clear: bool,
    dry_run: bool,
) -> Counter:
    rows = fetch_image_occurrence(revision, project, cache_dir)
    accessions = select_accessions(rows, trust_index)
    project_dir = ensure_dicoms(revision, project, cache_dir)
    if missing := missing_accessions(project_dir, accessions):
        raise SystemExit(
            f"❌ {project}: {len(missing)} of trust {trust_index}'s {len(accessions)} accessions have no DICOM "
            f"directory in dicom/{project}.tar.gz @ {revision} (e.g. {missing[:3]}). The OMOP slice and the DICOM "
            "set disagree — nothing uploaded. Re-publish the DICOM set with publish_dicom.py --verify-only."
        )
    files = [p for a in accessions for p in sorted((project_dir / a).glob("*.dcm"))]
    print(f"📦 {project}: trust {trust_index} owns {len(accessions)} studies / {len(files)} instances", flush=True)
    if dry_run:
        return Counter({"dry-run": len(files)})
    if clear:
        print(f"🧹 {project}: removing existing studies for these accessions …", flush=True)
        print(f"   removed {delete_studies(session, orthanc_url, accessions)} study(ies)", flush=True)
    outcomes: Counter = Counter()
    for i, path in enumerate(files, 1):
        outcomes[upload_instance(session, orthanc_url, path.read_bytes())] += 1
        if i % 500 == 0 or i == len(files):
            print(f"   {i}/{len(files)} — {dict(outcomes)}", flush=True)
    return outcomes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trust-index", type=int, required=True, help="1-based; matched against source_trust")
    parser.add_argument("--projects", nargs="+", default=DEFAULT_PROJECTS)
    parser.add_argument(
        "--revision",
        default=None,
        help=f"dataset revision to seed from: a data-version tag, main, or a sha. Default: $HF_TRUST_DATA_REVISION, "
        f"else the pinned tag in {DATA_VERSION_FILE}",
    )
    parser.add_argument("--orthanc-url", default=None, help="default: http://127.0.0.1:$PACS_UI_PORT")
    parser.add_argument("--cache-dir", type=Path, default=Path(__file__).resolve().parent / "volumes" / "dicom")
    parser.add_argument("--clear-projects", action="store_true", help="delete these projects' studies first")
    parser.add_argument("--dry-run", action="store_true", help="resolve and count; upload nothing")
    args = parser.parse_args(argv)

    revision = resolve_revision(args.revision)
    orthanc_url = args.orthanc_url or f"http://127.0.0.1:{os.environ['PACS_UI_PORT']}"
    session = requests.Session()
    session.auth = (os.environ["ORTHANC_USERNAME"], os.environ["ORTHANC_PASSWORD"])

    if not args.dry_run:
        session.get(f"{orthanc_url}/system", timeout=30).raise_for_status()

    total: Counter = Counter()
    for project in args.projects:
        total += seed_project(
            session, orthanc_url, revision, project, args.trust_index, args.cache_dir, args.clear_projects, args.dry_run
        )
    print(f"\n✅ trust {args.trust_index} @ {orthanc_url}: {dict(total)} across {args.projects} (revision {revision})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
