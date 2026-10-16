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

"""The ADNI → tutorial-cohort converter, exercised on tables shaped like the IDA downloads.

Every fixture here is invented in-process: the point is to pin the cohort rules (baseline visit,
±3-month scan matching, the earliest eligible scan, ADNI's missing-value codes, the FreeSurfer QC
count, left + right hippocampus) and the column contract shared with the tutorial's ``query.sql``,
so a licence holder's run produces the cohort the README describes. The second half writes the
converted frame *as* OMOP tables and runs the actual ``query.sql`` (both backends' byte-identical
copies) over them on SQLite, so the simulator CSV and a deployed fetch cannot drift apart. No ADNI
value appears in this file, and none may.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

FL_TUTORIALS_DIR = Path(__file__).resolve().parents[3]
SCRIPT_PATH = FL_TUTORIALS_DIR / "datasets" / "ad_biomarkers" / "make_adni_cohort.py"
QUERY_FILES = {
    "nvflare": FL_TUTORIALS_DIR / "nvflare" / "tabular_causal_discovery" / "biomarker_causal_discovery" / "query.sql",
    "flower": FL_TUTORIALS_DIR / "flower" / "biomarker_causal_discovery" / "query.sql",
}


@pytest.fixture(scope="module")
def converter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_adni_cohort", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_adni_cohort"] = module
    spec.loader.exec_module(module)
    return module


def _freesurfer_row(rid: int, examdate: str, icv: float, left: float, right: float, missing: int = 0) -> dict:
    """One UCSFFSX7-shaped row: the three volume columns plus 30 filler regions, ``missing`` of them NaN."""
    row = {"RID": rid, "EXAMDATE": examdate, "ST10CV": icv, "ST29SV": left, "ST88SV": right}
    for k in range(30):
        row[f"ST{200 + k}SV"] = np.nan if k < missing else 1000.0 + k
    return row


@pytest.fixture(scope="module")
def tables(tmp_path_factory) -> Path:
    """A five-table IDA download of nine participants, each probing one rule.

    RID 1  complete; two scans, the plasma draw sits beside the first → earliest scan, 2019 age
    RID 2  plasma draw five months after the scan → outside the window, dropped
    RID 3  two scans, plasma only within the window of the second → second scan kept
    RID 4  NfL_Q = -4 (failed assay) → dropped
    RID 5  complete; male, two ε4 alleles
    RID 6  education coded -1 → dropped
    RID 7  FreeSurfer row with 26 missing values → scan dropped → participant dropped
    RID 8  ADAS only at month 12, no baseline → dropped
    RID 9  right hippocampus missing → volume missing → dropped
    """
    adni_dir = tmp_path_factory.mktemp("adni_tables")
    pd.DataFrame(
        [
            {"RID": 1, "VISCODE2": "bl", "PTGENDER": 2, "PTEDUCAT": 16, "PTDOB": "06/1948"},
            {"RID": 1, "VISCODE2": "m12", "PTGENDER": 2, "PTEDUCAT": 18, "PTDOB": "06/1948"},  # long format: first kept
            {"RID": 2, "VISCODE2": "bl", "PTGENDER": 1, "PTEDUCAT": 12, "PTDOB": "01/1950"},
            {"RID": 3, "VISCODE2": "bl", "PTGENDER": 2, "PTEDUCAT": 20, "PTDOB": "03/1945"},
            {"RID": 4, "VISCODE2": "bl", "PTGENDER": 1, "PTEDUCAT": 14, "PTDOB": "09/1952"},
            {"RID": 5, "VISCODE2": "bl", "PTGENDER": 1, "PTEDUCAT": 13, "PTDOB": "11/1940"},
            {"RID": 6, "VISCODE2": "bl", "PTGENDER": 2, "PTEDUCAT": -1, "PTDOB": "02/1955"},
            {"RID": 7, "VISCODE2": "bl", "PTGENDER": 2, "PTEDUCAT": 15, "PTDOB": "02/1955"},
            {"RID": 8, "VISCODE2": "bl", "PTGENDER": 1, "PTEDUCAT": 17, "PTDOB": "02/1955"},
            {"RID": 9, "VISCODE2": "bl", "PTGENDER": 1, "PTEDUCAT": 17, "PTDOB": "02/1955"},
        ]
    ).to_csv(adni_dir / "PTDEMOG_14Oct2026.csv", index=False)
    pd.DataFrame(
        [{"RID": r, "GENOTYPE": g} for r, g in [(1, "3/4"), (2, "3/3"), (3, "3/3"), (4, "2/4"), (5, "4/4")]]
        + [{"RID": r, "GENOTYPE": "3/3"} for r in (6, 7, 8, 9)]
    ).to_csv(adni_dir / "APOERES_14Oct2026.csv", index=False)
    pd.DataFrame(
        [
            {"RID": 1, "VISCODE2": "bl", "TOTAL13": 12.0},
            {"RID": 1, "VISCODE2": "bl", "TOTAL13": 99.0},  # duplicate baseline: first kept
            {"RID": 1, "VISCODE2": "m12", "TOTAL13": 15.0},
            {"RID": 2, "VISCODE2": "bl", "TOTAL13": 8.0},
            {"RID": 3, "VISCODE2": "bl", "TOTAL13": 20.5},
            {"RID": 4, "VISCODE2": "bl", "TOTAL13": 9.0},
            {"RID": 5, "VISCODE2": "bl", "TOTAL13": 30.0},
            {"RID": 6, "VISCODE2": "bl", "TOTAL13": 10.0},
            {"RID": 7, "VISCODE2": "bl", "TOTAL13": 10.0},
            {"RID": 8, "VISCODE2": "m12", "TOTAL13": 10.0},
            {"RID": 9, "VISCODE2": "bl", "TOTAL13": 10.0},
        ]
    ).to_csv(adni_dir / "ADAS_14Oct2026.csv", index=False)
    pd.DataFrame(
        [
            _freesurfer_row(1, "2019-01-10", 1_500_000, 3600, 3700),
            _freesurfer_row(1, "2021-01-10", 1_500_100, 3500, 3600),
            _freesurfer_row(2, "2019-01-10", 1_400_000, 3000, 3100),
            _freesurfer_row(3, "2018-05-01", 1_450_000, 3900, 3950),
            _freesurfer_row(3, "2020-05-01", 1_450_500, 3800, 3850),
            _freesurfer_row(4, "2019-01-10", 1_400_000, 3000, 3100),
            _freesurfer_row(5, "2019-07-01", 1_600_000, 3300, 3400),
            _freesurfer_row(6, "2019-01-10", 1_400_000, 3000, 3100),
            _freesurfer_row(7, "2019-01-10", 1_400_000, 3000, 3100, missing=26),
            _freesurfer_row(8, "2019-01-10", 1_400_000, 3000, 3100),
            _freesurfer_row(9, "2019-01-10", 1_400_000, 3000, np.nan),
        ]
    ).to_csv(adni_dir / "UCSFFSX7_14Oct2026.csv", index=False)
    panel = {"AB42_F": 200.0, "AB40_F": 2500.0, "pT217_F": 0.2, "NfL_Q": 20.0, "GFAP_Q": 150.0}
    pd.DataFrame(
        [
            {"RID": 1, "VISCODE2": "bl", "EXAMDATE": "2019-02-01", **panel},
            {"RID": 1, "VISCODE2": "m24", "EXAMDATE": "2021-01-12", **panel},  # closer to scan 2, not baseline
            {"RID": 2, "VISCODE2": "bl", "EXAMDATE": "2019-06-10", **panel},
            {"RID": 3, "VISCODE2": "bl", "EXAMDATE": "2020-06-15", **panel, "pT217_F": 0.5},
            {"RID": 4, "VISCODE2": "bl", "EXAMDATE": "2019-01-20", **panel, "NfL_Q": -4},
            {"RID": 5, "VISCODE2": "bl", "EXAMDATE": "2019-05-01", **panel, "AB42_F": 100.0},
            {"RID": 6, "VISCODE2": "bl", "EXAMDATE": "2019-01-20", **panel},
            {"RID": 7, "VISCODE2": "bl", "EXAMDATE": "2019-01-20", **panel},
            {"RID": 8, "VISCODE2": "bl", "EXAMDATE": "2019-01-20", **panel},
            {"RID": 9, "VISCODE2": "bl", "EXAMDATE": "2019-01-20", **panel},
        ]
    ).to_csv(adni_dir / "UPENN_PLASMA_FUJIREBIO_QUANTERIX_14Oct2026.csv", index=False)
    # A phase-split ADAS table beside the consolidated one must not confuse the prefix match.
    pd.DataFrame([{"RID": 1, "TOTALMOD": 1.0}]).to_csv(adni_dir / "ADASSCORES_14Oct2026.csv", index=False)
    return adni_dir


@pytest.fixture(scope="module")
def cohort(converter: ModuleType, tables: Path) -> pd.DataFrame:
    paths = {name: converter.find_table(tables, name) for name in converter.TABLE_PREFIXES}
    return converter.build_cohort(
        paths["demographics"], paths["apoe"], paths["adas"], paths["freesurfer"], paths["plasma"]
    )


# --- the cohort rules ------------------------------------------------------------------------


def test_only_participants_with_every_variable_survive(converter: ModuleType, cohort: pd.DataFrame):
    assert list(cohort.columns) == converter.COLUMNS
    assert cohort["person_id"].tolist() == [1, 2, 3]  # RIDs 1, 3, 5 in RID order; 2, 4, 6, 7, 8, 9 dropped
    assert cohort[[c for c in cohort.columns if c != "person_id"]].notna().all().all()


def test_earliest_eligible_scan_and_baseline_draw(cohort: pd.DataFrame):
    rid1, rid3 = cohort.iloc[0], cohort.iloc[1]
    # RID 1: the first scan (2019) is matched to the baseline draw; the 2021 scan's closer m24 draw is ignored.
    assert rid1["icv_mm3"] == 1_500_000
    assert rid1["hippocampus_mm3"] == 3600 + 3700
    assert rid1["age"] == 2019 - 1948
    # RID 3: no baseline draw within 3 months of the 2018 scan, so the 2020 scan is the earliest eligible one.
    assert rid3["icv_mm3"] == 1_450_500
    assert rid3["plasma_ptau217"] == 0.5
    assert rid3["age"] == 2020 - 1945


def test_variable_derivations(cohort: pd.DataFrame):
    rid1, rid5 = cohort.iloc[0], cohort.iloc[2]
    assert (rid1["is_female"], rid5["is_female"]) == (1, 0)
    assert (rid1["apoe4_copies"], rid5["apoe4_copies"]) == (1, 2)
    assert rid1["education_years"] == 16  # the first demographics record, not the m12 one
    assert rid1["adas_cog13"] == 12.0  # the first baseline ADAS row, not the duplicate
    assert rid1["plasma_ab42_ab40_ratio"] == pytest.approx(200.0 / 2500.0)
    assert rid5["plasma_ab42_ab40_ratio"] == pytest.approx(100.0 / 2500.0)
    for column in ("age", "is_female", "education_years", "apoe4_copies"):
        assert np.issubdtype(cohort[column].dtype, np.integer), column


def test_find_table_by_prefix(converter: ModuleType, tables: Path, tmp_path: Path):
    assert converter.find_table(tables, "adas").name == "ADAS_14Oct2026.csv"
    assert converter.find_table(tables, "plasma").name.startswith("UPENN_PLASMA")
    with pytest.raises(converter.AdniTableError, match="no PTDEMOG"):
        converter.find_table(tmp_path, "demographics")
    (tmp_path / "PTDEMOG_01Jan2026.csv").write_text("RID\n")
    (tmp_path / "PTDEMOG_02Jan2026.csv").write_text("RID\n")
    with pytest.raises(converter.AdniTableError, match="several PTDEMOG"):
        converter.find_table(tmp_path, "demographics")


def test_missing_column_is_named(converter: ModuleType, tmp_path: Path):
    bad = tmp_path / "APOERES_x.csv"
    pd.DataFrame({"RID": [1], "APGEN1": [3]}).to_csv(bad, index=False)
    with pytest.raises(converter.AdniTableError, match=r"GENOTYPE"):
        converter.load_apoe(bad)


def test_cli_writes_splits_and_a_counts_only_summary(converter: ModuleType, tables: Path, tmp_path: Path, capsys):
    out = tmp_path / "cohort"
    assert converter.main(["--adni-dir", str(tables), "--output-dir", str(out), "--num-sites", "2"]) == 0
    frame = pd.read_csv(out / "dataframe.csv")
    site1 = pd.read_csv(out / "site1" / "dataframe.csv")
    site2 = pd.read_csv(out / "site2" / "dataframe.csv")
    assert site1["person_id"].tolist() == [2]  # site 1 holds person_id % num_sites == 0
    assert site2["person_id"].tolist() == [1, 3]
    assert len(site1) + len(site2) == len(frame) == 3
    summary = (out / "cohort_summary.json").read_text()
    assert '"participants": 3' in summary
    assert "ST10CV" not in summary  # counts and column names only
    printed = capsys.readouterr().out
    assert "wrote 3 participants" in printed
    assert "1500000" not in printed  # the script never prints a value


def test_explicit_paths_override_the_directory(converter: ModuleType, tables: Path, tmp_path: Path):
    out = tmp_path / "cohort"
    args = ["--output-dir", str(out)]
    for name in converter.TABLE_PREFIXES:
        args += [f"--{name}", str(converter.find_table(tables, name))]
    assert converter.main(args) == 0
    assert (out / "dataframe.csv").exists()


# --- query.sql parity -----------------------------------------------------------------------


def run_query(query_file: Path, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Run the tutorial's query.sql on SQLite over the OMOP tables (schema ``omop`` attached)."""
    con = sqlite3.connect(":memory:")
    con.execute("ATTACH DATABASE ':memory:' AS omop")
    for name, table in tables.items():
        # pandas' plain-sqlite3 writer ignores schema=, so stage in main and move under omop.
        table.to_sql(name, con, index=False)
        con.execute(f"CREATE TABLE omop.{name} AS SELECT * FROM main.{name}")
        con.execute(f"DROP TABLE main.{name}")
    return pd.read_sql_query(query_file.read_text(), con)


@pytest.mark.parametrize("backend", sorted(QUERY_FILES))
def test_query_sql_returns_the_converted_frame(converter: ModuleType, cohort: pd.DataFrame, backend: str):
    result = run_query(QUERY_FILES[backend], converter.to_omop_tables(cohort))
    assert list(result.columns) == converter.COLUMNS
    expected = cohort.sort_values("person_id").reset_index(drop=True)
    result = result.sort_values("person_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(result.astype(float), expected.astype(float), check_exact=False, atol=1e-9)


def test_query_sql_drops_persons_missing_any_measurement(converter: ModuleType, cohort: pd.DataFrame):
    tables = converter.to_omop_tables(cohort)
    victim = int(cohort["person_id"].iloc[0])
    m = tables["measurement"]
    tables["measurement"] = m[~((m["person_id"] == victim) & (m["measurement_source_value"] == "PLASMA_NFL"))]
    result = run_query(QUERY_FILES["nvflare"], tables)
    assert victim not in set(result["person_id"])
    assert len(result) == len(cohort) - 1


# --- rules a reviewer asked to pin -----------------------------------------------------------


def test_earliest_scan_is_decided_by_date_not_by_string_order(converter: ModuleType, tmp_path: Path):
    """A non-ISO export ('M/D/YYYY') must still put the 2011 scan before the 2012 one."""
    rows = [
        _freesurfer_row(1, "10/5/2012", 1_600_000, 3000, 3000),
        _freesurfer_row(1, "9/1/2011", 1_500_000, 3600, 3700),
    ]
    path = tmp_path / "UCSFFSX7_x.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    mri = converter.load_mri(path)
    assert mri["EXAMDATE"].tolist() == [pd.Timestamp("2011-09-01"), pd.Timestamp("2012-10-05")]
    assert mri["icv_mm3"].tolist() == [1_500_000, 1_600_000]


def test_non_positive_assay_values_count_as_missing(converter: ModuleType, tmp_path: Path):
    """-4 is ADNI's code, but a 0 or a stray -1 is just as impossible for an assay and must not reach the log."""
    panel = {"AB42_F": 200.0, "AB40_F": 2500.0, "pT217_F": 0.2, "NfL_Q": 20.0, "GFAP_Q": 150.0}
    rows = [
        {"RID": 1, "VISCODE2": "bl", "EXAMDATE": "2019-01-01", **panel},
        {"RID": 2, "VISCODE2": "bl", "EXAMDATE": "2019-01-01", **panel, "pT217_F": 0.0},
        {"RID": 3, "VISCODE2": "bl", "EXAMDATE": "2019-01-01", **panel, "GFAP_Q": -1},
        {"RID": 4, "VISCODE2": "bl", "EXAMDATE": "2019-01-01", **panel, "AB40_F": -4},
    ]
    path = tmp_path / "UPENN_PLASMA_FUJIREBIO_QUANTERIX_x.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    assert converter.load_plasma(path)["RID"].tolist() == [1]
