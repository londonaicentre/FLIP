#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydicom>=3.0.1"]
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
"""Verify a project's DICOM source against its published OMOP tables, then package it for HF.

The per-project DICOM sets that ``seed_orthanc.py`` loads are published to ``aicentreflip/trust-data``
as ``dicom/<project>.tar.gz`` — one archive per project, one copy (the data version is the tag on the
dataset commit that carries it, see ``trust/publish_trust_data.py``), ``<accession>/*.dcm`` inside, the
partition deliberately NOT baked into the layout (the seeder selects a trust's slice from the OMOP
``source_trust`` column). This is the tool that produces those archives, and it refuses to produce one
that the seeder could not later resolve completely (FLIP#1100).

What the source is checked against is either the tables already published at a dataset revision
(``--revision``: a data-version tag, or ``main``) or, for a project that is not published yet, the
locally generated canonical tree (``--tables-dir <dir>`` holding ``<project>/<table>.csv``). The
latter is the pre-publish check: verify against the tables you are about to upload, then publish
tables and archive together in one commit (``trust/publish_trust_data.py``).

Usage::

    uv run trust/orthanc/publish_dicom.py --project spleen_project --revision 20260729 \\
        --source /path/to/dicom_output.zip --fill-empty-numbers \\
        --out dist/dicom/spleen_project.tar.gz

    uv run trust/orthanc/publish_dicom.py --project prostate_project \\
        --tables-dir ../../fl-tutorials/data/prostate/canonical \\
        --source ../../fl-tutorials/data/prostate/dicom --out dist/dicom/prostate_project.tar.gz

    uv run trust/orthanc/publish_dicom.py --project cxr_project --revision main \\
        --source /path/to/omop_cxr.zip --verify-only

``--source`` is a ``.zip`` or a directory; every ``*.dcm`` under it is read and grouped by its
**AccessionNumber tag**, never by directory name, so any generator layout works. Verification is
exact, both ways: the set of accession numbers in the source must equal the set in the published
``image_occurrence.csv``, every StudyInstanceUID and PatientID must be published, and the per-trust
split by ``source_trust`` is reported. Any miss is a non-zero exit before anything is written.

``--fill-empty-numbers`` stamps ``AcquisitionNumber`` / ``SeriesNumber`` = 1 on instances where the tag
is present but empty — the defect in the original spleen output that makes MONAI Deploy's loader drop
every CT instance (see ``docs/source/working-with-flip-apps/package-model-as-map.rst``). Absent tags
are left absent (the cxr data), populated ones untouched. Applied at publish time, once, so the
seeder never has to know.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import tarfile
import urllib.request
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pydicom

HF_TRUST_DATA_REPO = os.environ.get("HF_TRUST_DATA_REPO", "aicentreflip/trust-data")


def hf_url(revision: str, path: str, repo: str = HF_TRUST_DATA_REPO) -> str:
    """URL of one dataset file at a revision. The version is the revision, never part of the path."""
    return f"https://huggingface.co/datasets/{repo}/resolve/{revision}/{path}"


# The two tags the 2026-08-21 in-place patch of the published Orthanc data filled (commit 113b13db),
# and the value the MAP guide documents for the same workaround.
FILLABLE_TAGS = ("AcquisitionNumber", "SeriesNumber")
FILL_VALUE = 1


@dataclass
class Instance:
    """What verification and packaging need from one DICOM file."""

    member: str  # path inside the source (zip member name or path relative to the dir)
    accession: str
    study_uid: str
    patient_id: str
    sop_uid: str
    empty_tags: tuple[str, ...] = field(default_factory=tuple)


class Source:
    """A ``.zip`` or a directory, read the same way: iterate ``*.dcm`` members, open one by name."""

    def __init__(self, path: Path):
        self.path = path
        self._zip = zipfile.ZipFile(path) if path.is_file() else None

    def members(self) -> list[str]:
        if self._zip:
            return sorted(n for n in self._zip.namelist() if n.lower().endswith(".dcm"))
        return sorted(str(p.relative_to(self.path)) for p in self.path.rglob("*.dcm"))

    def read(self, member: str) -> bytes:
        if self._zip:
            return self._zip.read(member)
        return (self.path / member).read_bytes()


def read_instances(source: Source) -> Iterator[Instance]:
    """Read the identifying tags of every instance in the source, headers only."""
    members = source.members()
    for i, member in enumerate(members, 1):
        ds = pydicom.dcmread(io.BytesIO(source.read(member)), stop_before_pixels=True)
        empty = tuple(t for t in FILLABLE_TAGS if t in ds and ds.get(t) in (None, ""))
        yield Instance(
            member=member,
            accession=str(ds.AccessionNumber),
            study_uid=str(ds.StudyInstanceUID),
            patient_id=str(ds.PatientID),
            sop_uid=str(ds.SOPInstanceUID),
            empty_tags=empty,
        )
        if i % 1000 == 0 or i == len(members):
            print(f"  read {i}/{len(members)} instances", file=sys.stderr)


VERIFY_TABLES = ("image_occurrence", "person")


def fetch_csv(revision: str, project: str, table: str) -> list[dict[str, str]]:
    url = hf_url(revision, f"omop-csv/{project}/{table}.csv")
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310
        return list(csv.DictReader(io.TextIOWrapper(response, encoding="utf-8")))


def load_tables(project: str, revision: str | None, tables_dir: Path | None) -> dict[str, list[dict[str, str]]]:
    """The canonical tables the source is checked against: published at a revision, or a local tree.

    Args:
        project (str): Project directory, e.g. ``spleen_project``.
        revision (str | None): Dataset revision to read the published tables at.
        tables_dir (Path | None): A local canonical tree, ``<tables_dir>/<project>/<table>.csv`` — the
            pre-publish check for a project that is not on the dataset yet.

    Returns:
        dict[str, list[dict[str, str]]]: Rows of ``image_occurrence`` and ``person``.

    Raises:
        SystemExit: If a local table is missing, or neither/both sources are given.
    """
    if (revision is None) == (tables_dir is None):
        raise SystemExit("give exactly one of --revision (published tables) or --tables-dir (a local canonical tree)")
    if tables_dir is not None:
        tables = {}
        for table in VERIFY_TABLES:
            path = tables_dir / project / f"{table}.csv"
            if not path.is_file():
                raise SystemExit(f"missing canonical table: {path}")
            with path.open(newline="", encoding="utf-8") as handle:
                tables[table] = list(csv.DictReader(handle))
        return tables
    assert revision is not None
    return {table: fetch_csv(revision, project, table) for table in VERIFY_TABLES}


def verify(instances: list[Instance], tables: dict[str, list[dict[str, str]]]) -> tuple[bool, dict[str, int]]:
    """Check the source is exactly the project's imaging as the canonical tables describe it.

    Args:
        instances (list[Instance]): Every instance read from the source.
        tables (dict[str, list[dict[str, str]]]): ``image_occurrence`` and ``person`` rows (:func:`load_tables`).

    Returns:
        tuple[bool, dict[str, int]]: ``(ok, per-trust study counts)``.
    """
    published = tables["image_occurrence"]
    persons = tables["person"]
    pub_acc = {r["accession_id"] for r in published}
    pub_uid = {r["image_study_uid"] for r in published}
    pub_pid = {r["person_source_value"] for r in persons}
    trust_of = {r["accession_id"]: r["source_trust"] for r in published}

    src_acc = {i.accession for i in instances}
    problems: list[str] = []
    if missing := sorted(pub_acc - src_acc):
        problems.append(f"{len(missing)} published accession(s) have no DICOM in the source, e.g. {missing[:3]}")
    if extra := sorted(src_acc - pub_acc):
        problems.append(f"{len(extra)} accession(s) in the source are not published, e.g. {extra[:3]}")
    if bad_uid := sorted({i.study_uid for i in instances} - pub_uid):
        problems.append(f"{len(bad_uid)} StudyInstanceUID(s) not published, e.g. {bad_uid[:2]}")
    if bad_pid := sorted({i.patient_id for i in instances} - pub_pid):
        problems.append(f"{len(bad_pid)} PatientID(s) not published, e.g. {bad_pid[:3]}")
    studies_per_acc = defaultdict(set)
    for i in instances:
        studies_per_acc[i.accession].add(i.study_uid)
    if multi := [a for a, s in studies_per_acc.items() if len(s) > 1]:
        problems.append(f"{len(multi)} accession(s) span more than one study, e.g. {multi[:3]}")
    if dup := [s for s, n in Counter(i.sop_uid for i in instances).items() if n > 1]:
        problems.append(f"{len(dup)} duplicate SOPInstanceUID(s), e.g. {dup[:2]}")

    per_trust = Counter(trust_of[a] for a in src_acc & pub_acc)
    print(f"source: {len(instances)} instances, {len(src_acc)} accessions; published: {len(pub_acc)} studies")
    print(f"per source_trust: {dict(sorted(per_trust.items()))}")
    empties = Counter(t for i in instances for t in i.empty_tags)
    if empties:
        print(f"present-but-empty tags: {dict(empties)}")
    for p in problems:
        print(f"MISMATCH  {p}")
    return not problems, dict(per_trust)


def fill_empty_numbers(data: bytes) -> bytes:
    """Return the instance with present-but-empty AcquisitionNumber/SeriesNumber set to FILL_VALUE."""
    ds = pydicom.dcmread(io.BytesIO(data))
    for tag in FILLABLE_TAGS:
        if tag in ds and ds.get(tag) in (None, ""):
            setattr(ds, tag, FILL_VALUE)
    out = io.BytesIO()
    ds.save_as(out, enforce_file_format=True)
    return out.getvalue()


def package(source: Source, instances: list[Instance], out: Path, fill: bool) -> tuple[int, int]:
    """Write ``<accession>/<basename>`` for every instance into a gzipped tar. Returns (written, filled)."""
    names_seen: set[str] = set()
    written = filled = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        for i, inst in enumerate(instances, 1):
            arcname = f"{inst.accession}/{Path(inst.member).name}"
            if arcname in names_seen:
                raise SystemExit(f"two instances would land on the same archive path: {arcname}")
            names_seen.add(arcname)
            data = source.read(inst.member)
            if fill and inst.empty_tags:
                data = fill_empty_numbers(data)
                filled += 1
            info = tarfile.TarInfo(arcname)
            info.size = len(data)
            info.mtime = 0  # reproducible archives: content decides the bytes, not the clock
            tar.addfile(info, io.BytesIO(data))
            written += 1
            if i % 1000 == 0 or i == len(instances):
                print(f"  packaged {i}/{len(instances)}", file=sys.stderr)
    return written, filled


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, help="e.g. spleen_project")
    parser.add_argument(
        "--revision",
        default=None,
        help="dataset revision whose published omop-csv/<project> tables this DICOM set pairs with: a data-version "
        "tag, or main. Exactly one of --revision / --tables-dir",
    )
    parser.add_argument(
        "--tables-dir",
        type=Path,
        default=None,
        help="local canonical tree (<dir>/<project>/<table>.csv) to verify against instead — the pre-publish check "
        "for a project not on the dataset yet",
    )
    parser.add_argument("--source", required=True, type=Path, help="a .zip or a directory of *.dcm")
    parser.add_argument("--out", type=Path, help="archive to write, e.g. dist/dicom/<project>.tar.gz")
    parser.add_argument("--fill-empty-numbers", action="store_true", help=f"set empty {FILLABLE_TAGS} to {FILL_VALUE}")
    parser.add_argument("--verify-only", action="store_true", help="check the source and stop")
    args = parser.parse_args(argv)
    if not args.verify_only and not args.out:
        parser.error("--out is required unless --verify-only")

    tables = load_tables(args.project, args.revision, args.tables_dir)
    against = f"@ {args.revision}" if args.revision else f"in {args.tables_dir}"
    source = Source(args.source)
    print(f"reading {args.source} …", file=sys.stderr)
    instances = list(read_instances(source))
    ok, _ = verify(instances, tables)
    if not ok:
        print(f"\nVERIFY FAIL — {args.source} is not exactly {args.project} {against}; nothing written")
        return 1
    print(f"\nVERIFY PASS — {args.source} is exactly {args.project} {against}")
    if args.verify_only:
        return 0

    written, filled = package(source, instances, args.out, args.fill_empty_numbers)
    size = args.out.stat().st_size / 1e6
    print(f"wrote {args.out} — {written} instances, {filled} with {FILLABLE_TAGS} filled, {size:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
