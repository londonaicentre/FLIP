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

"""Check that generated OMOP tables reproduce the published export.

Fetches the published CSVs for the pinned data version and diffs them against locally generated
ones. This is the check that makes the vendored chain's faithfulness demonstrable rather than
assumed — see the design spec's "What is and is not reproducible" (FLIP#1092).

Project-agnostic: every dataset under ``fl-tutorials/datasets/`` gates through this one script,
selected with ``--project``. Tables absent from a project's published export are skipped rather
than failed (spleen ships ``measurement`` and no ``observation``; cxr the reverse), which is why
``TABLES`` is the union across projects and why a run that compares nothing is a hard failure.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

HF_BASE = "https://huggingface.co/datasets/aicentreflip/trust-data/resolve/main/omop-csv"
TABLES = (
    "person",
    "procedure_occurrence",
    "visit_occurrence",
    "image_occurrence",
    "image_feature",
    "measurement",
    "observation",
)


def fetch_published(version: str, project: str, table: str) -> pd.DataFrame | None:
    """Fetch one published table, or None when it is absent upstream.

    Args:
        version: Data version, e.g. the contents of ``trust/omop-db/.data_version``.
        project: Project directory on the dataset, e.g. ``spleen_project``.
        table: OMOP table name.

    Returns:
        pd.DataFrame | None: The published table, or None if it 404s (optional tables such as
        ``observation`` are not shipped by every project).
    """
    url = f"{HF_BASE}/{version}/{project}/{table}.csv"
    try:
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
            return pd.read_csv(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def compare(mine: pd.DataFrame, theirs: pd.DataFrame) -> tuple[bool, str]:
    """Compare a generated table against its published counterpart.

    The published export materialises some optional schema columns the converter omits — every one
    is empty there — so shared columns are compared for equality and published-only columns are
    required to be information-free. A published-only column carrying real data is a genuine gap.

    Args:
        mine: Generated table, with any ``trust`` column already dropped.
        theirs: Published table, with ``source_trust`` already dropped.

    Returns:
        tuple[bool, str]: Whether they match, and a one-line description.
    """
    extra = sorted(set(theirs.columns) - set(mine.columns))
    carrying = [c for c in extra if theirs[c].replace({0: None, "0": None}).notna().any()]
    if carrying:
        return False, f"published-only columns carry data: {carrying}"
    missing = sorted(set(mine.columns) - set(theirs.columns))
    if missing:
        return False, f"generated columns absent upstream: {missing}"
    shared = sorted(set(mine.columns) & set(theirs.columns))
    key = mine.columns[0]
    left = mine.sort_values(key).reset_index(drop=True)[shared]
    right = theirs.sort_values(key).reset_index(drop=True)[shared]
    if not left.equals(right):
        differing = next((c for c in shared if not left[c].equals(right[c])), "?")
        return False, f"{left.shape} vs {right.shape}, first differing column: {differing}"
    note = f"  (+{len(extra)} empty published-only col(s))" if extra else ""
    return True, f"{len(left)} rows x {len(shared)} cols{note}"


def main(argv: list[str] | None = None) -> int:
    """Run the comparison and return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-dir", type=Path, default=Path("omop"))
    parser.add_argument(
        "--project",
        required=True,
        help="Project directory on the dataset and under omop/<trust>/, e.g. spleen_project.",
    )
    parser.add_argument("--trusts", nargs="+", default=["trust_1", "trust_2"])
    parser.add_argument(
        "--data-version",
        default=None,
        help="Defaults to the contents of trust/omop-db/.data_version.",
    )
    args = parser.parse_args(argv)

    version = args.data_version
    if version is None:
        version = (Path(__file__).resolve().parents[3] / "trust/omop-db/.data_version").read_text().strip()

    failed: list[str] = []
    compared = 0
    for table in TABLES:
        theirs = fetch_published(version, args.project, table)
        if theirs is None:
            print(f"skip  {table}: not published for {args.project}")
            continue
        paths = [args.generated_dir / t / args.project / f"{table}.csv" for t in args.trusts]
        missing = [p for p in paths if not p.is_file()]
        if missing:
            failed.append(table)
            print(f"DIFF  {table}: generated file(s) missing: {[str(p) for p in missing]}")
            continue
        mine = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)
        ok, detail = compare(mine, theirs.drop(columns=["source_trust"], errors="ignore"))
        print(f"{'MATCH' if ok else 'DIFF '} {table}: {detail}")
        compared += 1
        if not ok:
            failed.append(table)

    print()
    if compared == 0:
        print(
            f"GATE FAIL — no tables were compared for {args.project} at version {version}. "
            "Check --data-version and that the dataset path still exists."
        )
        return 1
    if failed:
        print(f"GATE FAIL — {len(failed)} table(s) diverge from the published export: {failed}")
        return 1
    print(f"GATE PASS — every published table reproduces from {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
