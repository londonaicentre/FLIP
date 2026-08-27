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

"""Build the EHR risk-prediction tutorial dataframe from the public Synthea-in-OMOP dataset.

Downloads three OMOP CDM tables of the fully synthetic 1k-person Synthea dataset from the
AWS Open Data Registry (https://registry.opendata.aws/synthea-omop/ — anonymous HTTPS, no
credentials, ~5 MB total) and derives the training CSVs the tutorial's ``.env.app`` points at:

    <output-dir>/dataframe.csv           # every person (the DEV_DATAFRAME fallback)
    <output-dir>/site1/dataframe.csv     # person_id % num_sites == 0
    <output-dir>/site2/dataframe.csv     # person_id % num_sites == 1

The feature/label logic deliberately mirrors the tutorial's ``query.sql`` (the OMOP SQL a
deployed run sends to each trust): binary pre-diagnosis condition-history flags + visit and
condition counts + demographics, labelled with first type-2-diabetes diagnosis. Sites are
split by ``person_id`` modulo — the same convention ``omop_db_tools.dataset`` uses for the
mock trusts. Change either file's feature logic and change the other to match.

Note the 1k Synthea-OMOP export carries no numeric measurement values (``value_as_number``
is empty throughout), which is why the features are condition-history flags rather than
labs/vitals.

Run via the Makefile (``make -C fl-tutorials download-synthea-data``), which invokes:
    uv run --no-project --with pandas python utils/build_synthea_dataframe.py --output-dir ../../data/synthea
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

import pandas as pd

# The 1k-person split of the Synthea-in-OMOP dataset on the AWS Open Data Registry.
DEFAULT_BASE_URL = "https://synthea-omop.s3.amazonaws.com/synthea1k"
TABLES = ("person", "condition_occurrence", "visit_occurrence")

# Columns the derivation actually reads: schema drift upstream must fail loudly here, not
# surface as a silently empty feature.
REQUIRED_COLUMNS = {
    "person": ["person_id", "gender_concept_id", "year_of_birth"],
    "condition_occurrence": ["person_id", "condition_source_value", "condition_start_date"],
    "visit_occurrence": ["person_id", "visit_start_date"],
}

# SNOMED codes as Synthea emits them in *_source_value. Keep in lockstep with query.sql.
T2DM_CODE = "44054006"
CONDITION_FLAGS = {
    "has_prediabetes": "15777000",
    "has_obesity": "162864005",  # Body mass index 30+ - obesity
    "has_severe_obesity": "408512008",  # Body mass index 40+ - severely obese
    "has_hypertension": "38341003",  # Hypertensive disorder (this export does not use 59621000)
    "has_hyperlipidemia": "55822004",
}
FEMALE_GENDER_CONCEPT_ID = 8532
# The dataset was exported in January 2023; ages are computed against that year (not "today")
# so the derived features are deterministic. Mirrored in query.sql.
AGE_REFERENCE_YEAR = 2023

# Below this many positives per site the AUROC on a 20% validation split becomes mostly
# noise; the tutorial README documents switching the label (e.g. to another SNOMED code)
# as the fix.
MIN_POSITIVES_PER_SITE = 30


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, required=True, help="Where dataframe.csv + site splits land")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Where the raw OMOP CSVs are cached (default: <output-dir>/.synthea-raw)",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Base URL of the Synthea-OMOP CSV tables")
    parser.add_argument("--num-sites", type=int, default=2, help="Number of site splits to write")
    return parser.parse_args()


def fetch_tables(base_url: str, cache_dir: Path) -> dict[str, pd.DataFrame]:
    """Download (or reuse cached) raw OMOP tables and validate their schema."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    tables: dict[str, pd.DataFrame] = {}
    for table in TABLES:
        path = cache_dir / f"{table}.csv"
        if not path.exists():
            url = f"{base_url}/{table}.csv"
            print(f"⬇️  {url}")
            try:
                with urllib.request.urlopen(url, timeout=120) as response, open(path, "wb") as fh:
                    fh.write(response.read())
            except Exception as err:
                path.unlink(missing_ok=True)
                raise SystemExit(
                    f"❌ Could not download {url}: {err}\n"
                    "   The AWS Open Data Synthea-OMOP layout may have changed — see "
                    "https://registry.opendata.aws/synthea-omop/"
                ) from err
        frame = pd.read_csv(path, dtype={"condition_source_value": str}, low_memory=False)
        missing = [column for column in REQUIRED_COLUMNS[table] if column not in frame.columns]
        if missing:
            raise SystemExit(f"❌ {path} is missing expected column(s) {missing} — upstream schema drift?")
        tables[table] = frame
    return tables


def derive_features(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per person: demographics + pre-diagnosis history features + label.

    Mirrors query.sql: condition/visit events count only when they start strictly before the
    person's first T2DM diagnosis; never-diagnosed persons contribute their whole history.
    """
    person = tables["person"]
    conditions = tables["condition_occurrence"].copy()
    visits = tables["visit_occurrence"].copy()
    conditions["condition_start_date"] = pd.to_datetime(conditions["condition_start_date"])
    visits["visit_start_date"] = pd.to_datetime(visits["visit_start_date"])

    first_dx = (
        conditions.loc[conditions["condition_source_value"] == T2DM_CODE]
        .groupby("person_id")["condition_start_date"]
        .min()
        .rename("dx_date")
    )

    frame = person[["person_id", "gender_concept_id", "year_of_birth"]].merge(
        first_dx, on="person_id", how="left"
    )
    frame["label_t2dm"] = frame["dx_date"].notna().astype(int)
    frame["age"] = AGE_REFERENCE_YEAR - frame["year_of_birth"]
    frame["is_female"] = (frame["gender_concept_id"] == FEMALE_GENDER_CONCEPT_ID).astype(int)

    conditions = conditions.merge(first_dx, on="person_id", how="left")
    pre_dx_conditions = conditions[
        conditions["dx_date"].isna() | (conditions["condition_start_date"] < conditions["dx_date"])
    ]
    for feature, code in CONDITION_FLAGS.items():
        flagged = pre_dx_conditions.loc[pre_dx_conditions["condition_source_value"] == code, "person_id"].unique()
        frame[feature] = frame["person_id"].isin(flagged).astype(int)
    frame["n_prior_conditions"] = (
        frame["person_id"]
        .map(pre_dx_conditions.groupby("person_id")["condition_source_value"].nunique())
        .fillna(0)
        .astype(int)
    )

    visits = visits.merge(first_dx, on="person_id", how="left")
    pre_dx_visits = visits[visits["dx_date"].isna() | (visits["visit_start_date"] < visits["dx_date"])]
    frame["n_prior_visits"] = (
        frame["person_id"].map(pre_dx_visits.groupby("person_id").size()).fillna(0).astype(int)
    )

    # Scope the cohort to persons with at least one recorded condition — the same inclusion
    # criterion query.sql applies (WHERE EXISTS (condition_occurrence)). On a real trust this is
    # a "has clinical history" filter; keeping it here makes the local-sim CSV agree row-for-row
    # with the deployed query. Every T2DM-positive person has the diagnosis condition itself, so
    # this only ever drops history-free negatives.
    frame = frame[frame["person_id"].isin(conditions["person_id"])]

    # accession_id: this cohort fetches no imaging, but the FLIP dev client requires the
    # column (flip-utils FLIPStandardDev.get_dataframe) — the app ignores it. Same aliasing
    # as query.sql; do not remove it or `make run-tutorial` breaks.
    frame["accession_id"] = frame["person_id"].astype(str)

    columns = [
        "person_id",
        "accession_id",
        "age",
        "is_female",
        *CONDITION_FLAGS,
        "n_prior_conditions",
        "n_prior_visits",
        "label_t2dm",
    ]
    return frame[columns]


def write_outputs(frame: pd.DataFrame, output_dir: Path, num_sites: int) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_dir / "dataframe.csv", index=False)
    print(f"✅ {output_dir / 'dataframe.csv'}: {len(frame)} persons, {frame['label_t2dm'].sum()} positive")

    for site in range(1, num_sites + 1):
        site_frame = frame[frame["person_id"] % num_sites == site - 1]
        site_dir = output_dir / f"site{site}"
        site_dir.mkdir(parents=True, exist_ok=True)
        site_frame.to_csv(site_dir / "dataframe.csv", index=False)
        positives = int(site_frame["label_t2dm"].sum())
        print(f"✅ {site_dir / 'dataframe.csv'}: {len(site_frame)} persons, {positives} positive")
        if positives < MIN_POSITIVES_PER_SITE:
            print(
                f"⚠️  site{site} has only {positives} positives (<{MIN_POSITIVES_PER_SITE}); validation "
                "AUROC will be noisy — consider a more prevalent label (see the tutorial README).",
                file=sys.stderr,
            )


def main() -> None:
    args = parse_args()
    cache_dir = args.cache_dir or (args.output_dir / ".synthea-raw")
    tables = fetch_tables(args.base_url, cache_dir)
    frame = derive_features(tables)
    write_outputs(frame, args.output_dir, args.num_sites)


if __name__ == "__main__":
    main()
