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
"""Unit tests for the Synthea-in-OMOP EHR loader's transforms.

Most of these need no database. TestCleanReservedBandProvenance is the exception: it exercises
clean_reserved_band's actual DELETE statements against an in-memory SQLite database standing in for
Postgres (attached under the "omop" schema alias so the loader's schema-qualified SQL is unchanged) —
still no external service, so it stays a unit test. TestFetchSourceTables stubs urllib.request.urlopen
(cache reuse, download, and both loud failure modes — no network), and TestParseArgs / TestMain cover
the CLI wiring with main's collaborators stubbed.
"""

import sqlite3
import urllib.request
from pathlib import Path

import pandas as pd
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text

from omop_db_tools import synthea_ehr
from omop_db_tools.synthea_ehr import (
    DEFAULT_BASE_URL,
    PERSON_ID_OFFSET,
    REQUIRED_SOURCE_COLUMNS,
    SOURCE_TABLES,
    SYNTHEA_SOURCE_VALUE_PREFIX,
    _trust_person_ids,
    build_condition_rows,
    build_person_rows,
    build_visit_rows,
    fetch_source_tables,
    load_synthea_ehr,
    main,
    parse_args,
)


@pytest.fixture
def person() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "person_id": [1, 2, 3, 4],
            "gender_concept_id": [8507, 8532, 9999, 8532],  # 9999 is non-standard → zeroed
            "year_of_birth": [1980, 1975, 2000, 1990],
            "birth_datetime": ["1980-06-15", "1975-01-01", "2000-12-31", "1990-03-03"],
        }
    )


@pytest.fixture
def condition() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "person_id": [1, 1, 2, 4],
            "condition_source_value": ["44054006", "15777000", "38341003", "44054006"],
            "condition_start_date": ["2010-01-01", "2011-02-02", "2012-03-03", "2013-04-04"],
        }
    )


@pytest.fixture
def visit() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "person_id": [1, 2, 4],
            "visit_start_date": ["2010-01-01", "2012-03-03", "2013-04-04"],
            "visit_end_date": ["2010-01-02", None, "2013-04-05"],
        }
    )


def test_offset_is_even_and_clears_the_imaging_ceiling():
    # Even → the person_id-modulo per-trust split (computed on the raw id) is unchanged by the shift.
    # >= 1_000_000_000 → clear of the imaging cohorts' person_id ceiling: nhs_number_to_integer takes
    # only the first 9 digits of a real NHS number, so an imaging person_id can be anywhere up to
    # 999_999_999 (NOT merely "small" — it scatters across the whole 9-digit range, which is exactly
    # what made the old id-band *delete* unsafe; that safety now comes from clean_reserved_band's
    # provenance filter, not from this offset — this assertion only protects the INSERT path).
    assert PERSON_ID_OFFSET % 2 == 0
    assert PERSON_ID_OFFSET >= 1_000_000_000


def test_trust_split_is_disjoint_and_covering(person: pd.DataFrame):
    first = _trust_person_ids(person, num_trusts=2, trust_index=1)
    second = _trust_person_ids(person, num_trusts=2, trust_index=2)
    assert first.isdisjoint(second)
    assert first | second == set(person["person_id"])
    # trust_index 1 gets person_id % 2 == 0, i.e. the even ids.
    assert first == {2, 4}
    assert second == {1, 3}


def test_build_person_rows_offsets_and_sanitises_concepts(person: pd.DataFrame):
    rows = build_person_rows(person, {1, 2, 3})
    assert list(rows["person_id"]) == [1 + PERSON_ID_OFFSET, 2 + PERSON_ID_OFFSET, 3 + PERSON_ID_OFFSET]
    # Standard gender concepts survive; a non-standard one is zeroed to stay FK-safe.
    assert list(rows["gender_concept_id"]) == [8507, 8532, 0]
    assert (rows["race_concept_id"] == 0).all()
    assert (rows["ethnicity_concept_id"] == 0).all()
    # clean_reserved_band's delete keys off this prefix — it must actually be there.
    assert list(rows["person_source_value"]) == [
        f"{SYNTHEA_SOURCE_VALUE_PREFIX}1",
        f"{SYNTHEA_SOURCE_VALUE_PREFIX}2",
        f"{SYNTHEA_SOURCE_VALUE_PREFIX}3",
    ]
    assert set(rows.columns) == {
        "person_id",
        "gender_concept_id",
        "year_of_birth",
        "birth_datetime",
        "race_concept_id",
        "ethnicity_concept_id",
        "person_source_value",
    }


def test_build_person_rows_carries_birth_datetime(person: pd.DataFrame):
    # data-access-api's cohort statistics compute the age distribution from
    # omop.person.birth_datetime — a NULL there 500s every cohort submission for the tutorial,
    # so the loader must populate it and the schema guard must require it upstream.
    rows = build_person_rows(person, {1, 2, 3}).reset_index(drop=True)
    assert rows["birth_datetime"].notna().all()
    assert list(rows["birth_datetime"]) == list(pd.to_datetime(["1980-06-15", "1975-01-01", "2000-12-31"]))
    assert "birth_datetime" in REQUIRED_SOURCE_COLUMNS["person"]


def test_build_condition_rows_keeps_snomed_and_offsets_ids(condition: pd.DataFrame):
    rows = build_condition_rows(condition, {1, 4})
    # Only persons 1 and 4 (3 conditions), source SNOMED string preserved verbatim.
    assert list(rows["condition_source_value"]) == ["44054006", "15777000", "44054006"]
    assert (rows["person_id"] >= PERSON_ID_OFFSET).all()
    assert (rows["condition_occurrence_id"] >= PERSON_ID_OFFSET).all()
    assert rows["condition_occurrence_id"].is_unique
    # concept_id columns zeroed (query.sql matches on condition_source_value, not concept_id).
    assert (rows["condition_concept_id"] == 0).all()
    assert (rows["condition_type_concept_id"] == 0).all()


def test_build_visit_rows_falls_back_end_date_to_start(visit: pd.DataFrame):
    rows = build_visit_rows(visit, {1, 2, 4}).reset_index(drop=True)
    # person 2's visit_end_date is NULL → falls back to its start date, so the NOT NULL column loads.
    assert str(rows.loc[1, "visit_end_date"]) == "2012-03-03"
    assert (rows["person_id"] >= PERSON_ID_OFFSET).all()
    assert rows["visit_occurrence_id"].is_unique


def test_visit_end_date_defaults_to_start_when_column_absent():
    frame = pd.DataFrame({"person_id": [1], "visit_start_date": ["2020-05-05"]})
    rows = build_visit_rows(frame, {1})
    assert str(rows.loc[0, "visit_start_date"]) == "2020-05-05"
    assert str(rows.loc[0, "visit_end_date"]) == "2020-05-05"


class TestCleanReservedBandProvenance:
    """Regression test for the id-band delete bug.

    The imaging cohorts' person_id is nhs_number_to_integer(PatientID) — the first 9 digits of a
    real, effectively random NHS number — so it is not "small"; it scatters across the whole 9-digit
    range. 923226025 is a real person_id from the public spleen export, and it sits in the *old*
    reserved band (>= the retired 900_000_000 threshold): the previous "DELETE WHERE person_id >=
    PERSON_ID_OFFSET" would have destroyed this person, and cascaded away their image_occurrence /
    procedure_occurrence rows with them (fpk_image_occurrence_person_id is ON DELETE CASCADE).
    Measured live: a trust holding 4187 imaging persons lost 422 of them (and their imaging rows) to
    exactly this bug on every `make load-synthea-ehr` reload.

    clean_reserved_band must never delete this person — regardless of where its person_id falls —
    because it deletes by provenance (person_source_value), not by id range.
    """

    IMAGING_PERSON_ID = 923226025

    @pytest.fixture
    def engine(self):
        """An in-memory SQLite database with an attached "omop" schema mimicking the trust OMOP DB."""

        def creator() -> sqlite3.Connection:
            conn = sqlite3.connect(":memory:")
            conn.execute("ATTACH DATABASE ':memory:' AS omop")
            return conn

        engine = create_engine("sqlite://", creator=creator)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE omop.person (person_id INTEGER PRIMARY KEY, gender_concept_id INTEGER, "
                    "year_of_birth INTEGER, birth_datetime TEXT, race_concept_id INTEGER, "
                    "ethnicity_concept_id INTEGER, person_source_value TEXT)"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE omop.condition_occurrence (condition_occurrence_id INTEGER PRIMARY KEY, "
                    "person_id INTEGER, condition_concept_id INTEGER, condition_start_date TEXT, "
                    "condition_type_concept_id INTEGER, condition_source_value TEXT)"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE omop.visit_occurrence (visit_occurrence_id INTEGER PRIMARY KEY, "
                    "person_id INTEGER, visit_concept_id INTEGER, visit_start_date TEXT, "
                    "visit_end_date TEXT, visit_type_concept_id INTEGER)"
                )
            )
            # An imaging-cohort person (real NHS-number-derived id, no "synthea-" provenance marker)
            # with a visit row — exactly what fpk_image_occurrence_person_id's ON DELETE CASCADE
            # stands in for here.
            conn.execute(
                text(
                    "INSERT INTO omop.person "
                    "(person_id, gender_concept_id, year_of_birth, person_source_value) "
                    "VALUES (:pid, 8532, 1990, 'imaging-cohort')"
                ),
                {"pid": self.IMAGING_PERSON_ID},
            )
            conn.execute(
                text(
                    "INSERT INTO omop.visit_occurrence "
                    "(visit_occurrence_id, person_id, visit_concept_id, visit_start_date, "
                    "visit_end_date, visit_type_concept_id) VALUES (1, :pid, 0, '2020-01-01', '2020-01-01', 0)"
                ),
                {"pid": self.IMAGING_PERSON_ID},
            )
        return engine

    def test_imaging_person_in_old_danger_zone_survives_load_and_reload(
        self, engine, person: pd.DataFrame, condition: pd.DataFrame, visit: pd.DataFrame
    ):
        tables = {"person": person, "condition_occurrence": condition, "visit_occurrence": visit}

        load_synthea_ehr(engine, tables, num_trusts=1, trust_index=1)
        load_synthea_ehr(engine, tables, num_trusts=1, trust_index=1)  # reload must stay idempotent

        with engine.connect() as conn:
            surviving_person = conn.execute(
                text("SELECT person_id FROM omop.person WHERE person_id = :pid"),
                {"pid": self.IMAGING_PERSON_ID},
            ).fetchall()
            surviving_visit = conn.execute(
                text("SELECT visit_occurrence_id FROM omop.visit_occurrence WHERE person_id = :pid"),
                {"pid": self.IMAGING_PERSON_ID},
            ).fetchall()
        assert surviving_person, "an imaging person with an id in the old delete band was destroyed by a reload"
        assert surviving_visit, "the imaging person's visit_occurrence row was cascaded away"

    def test_synthea_rows_are_still_replaced_not_duplicated_on_reload(
        self, engine, person: pd.DataFrame, condition: pd.DataFrame, visit: pd.DataFrame
    ):
        tables = {"person": person, "condition_occurrence": condition, "visit_occurrence": visit}

        first_count = load_synthea_ehr(engine, tables, num_trusts=1, trust_index=1)
        second_count = load_synthea_ehr(engine, tables, num_trusts=1, trust_index=1)

        assert first_count == second_count == len(person)
        with engine.connect() as conn:
            (total,) = conn.execute(
                text("SELECT COUNT(*) FROM omop.person WHERE person_source_value LIKE :marker"),
                {"marker": f"{SYNTHEA_SOURCE_VALUE_PREFIX}%"},
            ).one()
        assert total == len(person)  # not doubled by the reload


# ---------------------------------------------------------------------------------------------
# fetch_source_tables: cache reuse, download, and the two loud failure modes — no network.
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def source_tables(person: pd.DataFrame, condition: pd.DataFrame, visit: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {"person": person, "condition_occurrence": condition, "visit_occurrence": visit}


def _write_cache(cache_dir: Path, tables: dict[str, pd.DataFrame]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in tables.items():
        frame.to_csv(cache_dir / f"{name}.csv", index=False)


class _FakeResponse:
    """Stands in for urlopen's response context manager: ``read()`` returns the body or raises it."""

    def __init__(self, body: bytes | Exception):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self) -> bytes:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class TestFetchSourceTables:
    BASE_URL = "https://example.test/synthea1k"

    def test_reuses_cached_csvs_without_touching_the_network(
        self, tmp_path: Path, source_tables: dict[str, pd.DataFrame], monkeypatch: pytest.MonkeyPatch
    ):
        cache_dir = tmp_path / "cache"
        # A SNOMED code with a leading zero proves the string dtype pin is load-bearing: read as an
        # int it would come back as 44054006 and the tutorial's condition_source_value match would lie.
        source_tables["condition_occurrence"].loc[0, "condition_source_value"] = "044054006"
        _write_cache(cache_dir, source_tables)

        def no_network(*args: object, **kwargs: object) -> None:
            raise AssertionError("urlopen must not be called when every table is already cached")

        monkeypatch.setattr(urllib.request, "urlopen", no_network)

        tables = fetch_source_tables(self.BASE_URL, cache_dir)

        assert set(tables) == set(SOURCE_TABLES)
        for name, frame in source_tables.items():
            assert len(tables[name]) == len(frame)
        assert tables["condition_occurrence"].loc[0, "condition_source_value"] == "044054006"

    def test_downloads_only_the_missing_tables_into_the_cache(
        self, tmp_path: Path, source_tables: dict[str, pd.DataFrame], monkeypatch: pytest.MonkeyPatch
    ):
        cache_dir = tmp_path / "cache"
        _write_cache(cache_dir, {"person": source_tables["person"]})  # person is already cached
        requested: list[str] = []

        def fake_urlopen(url: str, timeout: float) -> _FakeResponse:
            requested.append(url)
            table = url.rsplit("/", 1)[1].removesuffix(".csv")
            return _FakeResponse(source_tables[table].to_csv(index=False).encode())

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        tables = fetch_source_tables(self.BASE_URL, cache_dir)

        assert requested == [f"{self.BASE_URL}/condition_occurrence.csv", f"{self.BASE_URL}/visit_occurrence.csv"]
        assert {path.name for path in cache_dir.iterdir()} == {f"{table}.csv" for table in SOURCE_TABLES}
        assert set(tables) == set(SOURCE_TABLES)
        assert list(tables["visit_occurrence"]["person_id"]) == [1, 2, 4]

    def test_download_failure_exits_loudly_and_leaves_no_partial_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        cache_dir = tmp_path / "cache"
        # The response opens fine but dies mid-read: by then the output file already exists, and a
        # truncated person.csv left behind would be silently "cached" and reused on the next run.
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda url, timeout: _FakeResponse(OSError("connection reset"))
        )

        with pytest.raises(SystemExit, match=r"Could not download .*/person\.csv: connection reset") as excinfo:
            fetch_source_tables(self.BASE_URL, cache_dir)

        assert isinstance(excinfo.value.__cause__, OSError)
        assert "registry.opendata.aws/synthea-omop" in str(excinfo.value)
        assert not (cache_dir / "person.csv").exists()

    @pytest.mark.parametrize("table", SOURCE_TABLES)
    def test_missing_required_column_exits_naming_it(
        self, tmp_path: Path, source_tables: dict[str, pd.DataFrame], table: str
    ):
        cache_dir = tmp_path / "cache"
        dropped = REQUIRED_SOURCE_COLUMNS[table][-1]
        source_tables[table] = source_tables[table].drop(columns=[dropped])
        _write_cache(cache_dir, source_tables)

        expected = rf"{table}\.csv is missing expected column\(s\) \['{dropped}'\]"
        with pytest.raises(SystemExit, match=expected):
            fetch_source_tables(self.BASE_URL, cache_dir)


# ---------------------------------------------------------------------------------------------
# CLI: argument parsing and main's wiring (collaborators stubbed — no network, no database).
# ---------------------------------------------------------------------------------------------


class TestParseArgs:
    def test_defaults(self):
        args = parse_args(["--trust-index", "1"])
        assert args.trust_index == 1
        assert args.num_trusts == 2
        assert args.base_url == DEFAULT_BASE_URL
        assert args.cache_dir == Path("data/synthea-ehr")

    def test_overrides_are_typed(self, tmp_path: Path):
        args = parse_args(
            ["--trust-index", "2", "--num-trusts", "3", "--base-url", "https://example.test/x"]
            + ["--cache-dir", str(tmp_path)]
        )
        assert (args.trust_index, args.num_trusts) == (2, 3)
        assert args.base_url == "https://example.test/x"
        assert args.cache_dir == tmp_path  # a Path, not the raw string

    def test_trust_index_is_required(self):
        with pytest.raises(SystemExit) as excinfo:
            parse_args([])
        assert excinfo.value.code == 2  # argparse usage error


class TestMain:
    @pytest.fixture
    def wiring(self, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, object], dict[str, pd.DataFrame], object]:
        """Stub main's collaborators and record how each was called."""
        calls: dict[str, object] = {}
        tables = {"person": pd.DataFrame({"person_id": [1]})}
        engine = object()

        def fake_fetch(base_url: str, cache_dir: Path) -> dict[str, pd.DataFrame]:
            calls["fetch"] = (base_url, cache_dir)
            return tables

        class FakeSettings:
            OMOP_DATABASE_URL = SecretStr("postgresql://omop-host:5434/omop")

        def fake_create_engine(url: str, echo: bool) -> object:
            calls["create_engine"] = (url, echo)
            return engine

        def fake_load(
            engine_arg: object, tables_arg: dict[str, pd.DataFrame], num_trusts: int, trust_index: int
        ) -> int:
            calls["load"] = (engine_arg, tables_arg, num_trusts, trust_index)
            return 1

        monkeypatch.setattr(synthea_ehr, "fetch_source_tables", fake_fetch)
        monkeypatch.setattr(synthea_ehr, "get_settings", lambda: FakeSettings())
        monkeypatch.setattr(synthea_ehr, "create_engine", fake_create_engine)
        monkeypatch.setattr(synthea_ehr, "load_synthea_ehr", fake_load)
        return calls, tables, engine

    def test_wires_fetch_engine_and_load_from_the_cli_arguments(self, wiring, tmp_path: Path, capsys):
        calls, tables, engine = wiring

        main(
            ["--trust-index", "2", "--num-trusts", "3", "--base-url", "https://example.test/synthea"]
            + ["--cache-dir", str(tmp_path)]
        )

        assert calls["fetch"] == ("https://example.test/synthea", tmp_path)
        # The engine gets the unwrapped connection string, never the SecretStr wrapper.
        assert calls["create_engine"] == ("postgresql://omop-host:5434/omop", False)
        assert calls["load"] == (engine, tables, 3, 2)
        assert "Synthea EHR cohort loaded" in capsys.readouterr().out

    @pytest.mark.parametrize(("trust_index", "num_trusts"), [(0, 2), (3, 2), (1, 0)])
    def test_rejects_an_out_of_range_trust_index_before_any_download(self, wiring, trust_index: int, num_trusts: int):
        calls, _, _ = wiring

        expected = rf"Need 1 <= --trust-index <= --num-trusts \(got {trust_index}/{num_trusts}\)"
        with pytest.raises(SystemExit, match=expected):
            main(["--trust-index", str(trust_index), "--num-trusts", str(num_trusts)])

        assert calls == {}  # neither the download nor the engine was reached
