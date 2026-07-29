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
"""Canonical mock-dataset handling: build, fetch, and per-trust splitting.

The mock OMOP rows live as ONE canonical dataset (per project/cohort, one CSV per
table) published to the public Hugging Face dataset ``aicentreflip/trust-data``
under ``omop-csv/<version>/``. Every row carries a ``source_trust`` provenance
column recording which of the two originally-generated trusts it came from.

Standing up N trusts is a deterministic split of that single dataset:

- ``legacy`` (default): partition by ``source_trust``. Reproduces the exact
  original two-trust membership, keeping each trust's OMOP rows consistent with
  the accession IDs present in that trust's published mock PACS (Orthanc) data.
- ``modulo``: partition by ``person_id % num_trusts``. Supports any trust count
  for fresh stand-ups where the imaging data is regenerated to match.

Every canonical table carries ``person_id``, so partitioning by person preserves
referential integrity across tables without re-keying.
"""

import argparse
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

# Insert order: person first. Populate targets constraint-free databases
# (constraints are applied only after the load), so order is convention, not FK
# correctness.
CANONICAL_TABLES = [
    "person",
    "procedure_occurrence",
    "visit_occurrence",
    "image_occurrence",
    "image_feature",
    "measurement",
    "observation",
]

# Not every project ships every table (e.g. the cxr project has observations but
# no measurements; spleen the other way round).
OPTIONAL_TABLES = frozenset({"measurement", "observation"})

DEFAULT_PROJECTS = ["cxr_project", "spleen_project"]

SOURCE_TRUST_COLUMN = "source_trust"

HF_OMOP_CSV_BASE_URL = "https://huggingface.co/datasets/aicentreflip/trust-data/resolve/main/omop-csv"

PARTITION_MODES = ["legacy", "modulo"]


def split_for_trust(df: pd.DataFrame, num_trusts: int, trust_index: int, mode: str = "legacy") -> pd.DataFrame:
    """Return the deterministic slice of a canonical table belonging to one trust.

    Args:
        df (pd.DataFrame): One canonical table (may carry the source_trust column).
        num_trusts (int): Total number of trusts being stood up.
        trust_index (int): 1-based index of the trust to extract.
        mode (str): "legacy" (partition by source_trust) or "modulo" (partition by
            person_id % num_trusts).

    Returns:
        pd.DataFrame: The trust's rows, with the provenance column dropped.

    Raises:
        ValueError: On invalid arguments, or when the requested mode cannot
            partition the given frame (missing column / trust-count mismatch).
    """
    if num_trusts < 1:
        raise ValueError(f"num_trusts must be >= 1, got {num_trusts}")
    if not 1 <= trust_index <= num_trusts:
        raise ValueError(f"trust_index must be in [1, {num_trusts}], got {trust_index}")
    if mode not in PARTITION_MODES:
        raise ValueError(f"Unknown partition mode {mode!r}; expected one of {PARTITION_MODES}")

    if df.empty:
        return df.drop(columns=[SOURCE_TRUST_COLUMN], errors="ignore")

    if "person_id" in df.columns and df["person_id"].isna().any():
        raise ValueError(
            "person_id contains missing values — such rows would silently belong to no partition; "
            "the canonical CSV is malformed"
        )

    if mode == "legacy":
        if SOURCE_TRUST_COLUMN not in df.columns:
            raise ValueError(
                f"Legacy partitioning needs the {SOURCE_TRUST_COLUMN!r} column; "
                "this frame does not carry it — use mode='modulo' instead"
            )
        sources = set(df[SOURCE_TRUST_COLUMN].unique())
        if sources != set(range(1, num_trusts + 1)):
            raise ValueError(
                f"Legacy partitioning needs {SOURCE_TRUST_COLUMN} values to be exactly 1..{num_trusts}; "
                f"this frame carries {sorted(sources)} — rebuild the canonical dataset or use mode='modulo'"
            )
        part = df[df[SOURCE_TRUST_COLUMN] == trust_index]
    else:
        if "person_id" not in df.columns:
            raise ValueError("Modulo partitioning needs a person_id column")
        part = df[df["person_id"] % num_trusts == trust_index - 1]

    return part.drop(columns=[SOURCE_TRUST_COLUMN], errors="ignore").reset_index(drop=True)


def build_canonical(trust_dirs: list[Path], dest_dir: Path, projects: list[str] | None = None) -> None:
    """Merge per-trust CSV exports into the canonical dataset with provenance.

    Args:
        trust_dirs (list[Path]): Per-trust source directories, in trust order
            (index i holds source_trust = i + 1); each contains <project>/<table>.csv.
        dest_dir (Path): Output directory for the canonical <project>/<table>.csv files.
        projects (list[str] | None): Projects to merge; defaults to DEFAULT_PROJECTS.

    Raises:
        FileNotFoundError: When a trust directory does not exist, a required
            table is absent from every source, or a table is present in some
            sources but missing from others (optionality is per-project by
            design — per-trust asymmetry means a broken or mistyped export).
    """
    for trust_dir in trust_dirs:
        if not trust_dir.is_dir():
            raise FileNotFoundError(f"Trust source directory does not exist: {trust_dir}")
    for project in projects or DEFAULT_PROJECTS:
        out_dir = dest_dir / project
        out_dir.mkdir(parents=True, exist_ok=True)
        for table in CANONICAL_TABLES:
            frames = []
            missing_from = []
            for trust_number, trust_dir in enumerate(trust_dirs, start=1):
                csv_path = trust_dir / project / f"{table}.csv"
                if not csv_path.is_file():
                    missing_from.append(str(trust_dir))
                    continue
                frame = pd.read_csv(csv_path)
                frame[SOURCE_TRUST_COLUMN] = trust_number
                frames.append(frame)
            if frames and missing_from:
                raise FileNotFoundError(
                    f"{project}/{table}.csv is present in some sources but missing from {missing_from} — "
                    "rows from those trusts would be silently dropped"
                )
            if not frames:
                if table not in OPTIONAL_TABLES:
                    raise FileNotFoundError(f"No source CSV found for required table {project}/{table}")
                print(f"⚠️  Optional table absent in all sources, skipping: {project}/{table}")
                continue
            merged = pd.concat(frames, ignore_index=True)
            merged.to_csv(out_dir / f"{table}.csv", index=False)
            print(f"✅ {project}/{table}.csv: {len(merged)} rows from {len(frames)} source trust(s)")


def fetch_canonical(
    version: str,
    dest_dir: Path,
    projects: list[str] | None = None,
    base_url: str = HF_OMOP_CSV_BASE_URL,
) -> None:
    """Download one version of the canonical dataset (anonymous HTTPS, no credentials).

    Args:
        version (str): Dataset version (the omop-csv/<version>/ prefix on Hugging Face).
        dest_dir (Path): Output directory for the canonical <project>/<table>.csv files.
        projects (list[str] | None): Projects to fetch; defaults to DEFAULT_PROJECTS.
        base_url (str): Base URL holding the versioned dataset tree.
    """
    for project in projects or DEFAULT_PROJECTS:
        out_dir = dest_dir / project
        out_dir.mkdir(parents=True, exist_ok=True)
        for table in CANONICAL_TABLES:
            url = f"{base_url}/{version}/{project}/{table}.csv"
            try:
                with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
                    content = response.read()
            except urllib.error.HTTPError as error:
                if error.code == 404 and table in OPTIONAL_TABLES:
                    print(f"⚠️  Optional table not in dataset, skipping: {project}/{table}")
                    continue
                raise RuntimeError(
                    f"Failed to fetch {url}: HTTP {error.code} (is version {version!r} published?)"
                ) from error
            if content.lstrip()[:1] == b"<":
                raise RuntimeError(
                    f"{url} returned HTML, not CSV — check base_url (Hugging Face needs /resolve/, not /blob/)"
                )
            (out_dir / f"{table}.csv").write_bytes(content)
            print(f"⬇️  {project}/{table}.csv ({len(content)} bytes)")


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: build or fetch the canonical dataset."""
    parser = argparse.ArgumentParser(description="Build or fetch the canonical mock OMOP dataset.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Merge per-trust CSV exports into the canonical dataset.")
    build_parser.add_argument(
        "--trust-dirs",
        nargs="+",
        type=Path,
        required=True,
        help="Per-trust source directories in trust order (e.g. data/trust_1 data/trust_2).",
    )
    build_parser.add_argument("--dest", type=Path, required=True, help="Output directory.")
    build_parser.add_argument("--projects", nargs="+", default=None, help="Projects to merge (default: all).")

    fetch_parser = subparsers.add_parser("fetch", help="Download the canonical dataset from Hugging Face.")
    fetch_parser.add_argument("--version", required=True, help="Dataset version (omop-csv/<version>/ prefix).")
    fetch_parser.add_argument("--dest", type=Path, required=True, help="Output directory.")
    fetch_parser.add_argument("--projects", nargs="+", default=None, help="Projects to fetch (default: all).")
    fetch_parser.add_argument("--base-url", default=HF_OMOP_CSV_BASE_URL, help="Override the dataset base URL.")

    args = parser.parse_args(argv)
    if args.command == "build":
        build_canonical(args.trust_dirs, args.dest, args.projects)
    else:
        fetch_canonical(args.version, args.dest, args.projects, args.base_url)


if __name__ == "__main__":
    main()
