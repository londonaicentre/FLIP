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
"""Snowflake OMOP seeding: SQL generation, the --if-needed check, partition filtering, unseed (FLIP#1580).

Nothing here touches a Snowflake account, a network or the ``snow`` CLI: the module's whole
design is that the SQL is produced first and executed (or printed) second, so the interesting
half is testable offline. ``run_sql`` is exercised through a stubbed executable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pandas as pd
import pytest

from omop_db_tools import snowflake_seed as seed


@pytest.fixture
def names() -> seed.Names:
    return seed.names_for("gstt")


@pytest.fixture
def canonical(tmp_path: Path) -> Path:
    """A two-trust canonical tree for one project, in the shape dataset.py publishes."""
    project_dir = tmp_path / "canonical" / "cxr_project"
    project_dir.mkdir(parents=True)
    person = pd.DataFrame(
        {
            "person_id": [1, 2, 3, 4],
            "gender_concept_id": [8507, 8532, 8507, 8532],
            "year_of_birth": [1970, 1980, 1990, 2000],
            "race_concept_id": [0, 0, 0, 0],
            "ethnicity_concept_id": [0, 0, 0, 0],
            "source_trust": [1, 1, 2, 2],
        }
    )
    person.to_csv(project_dir / "person.csv", index=False)
    for table in ("visit_occurrence", "procedure_occurrence", "image_occurrence", "image_feature"):
        pd.DataFrame({"person_id": [1, 3], f"{table}_id": [10, 30], "source_trust": [1, 2]}).to_csv(
            project_dir / f"{table}.csv", index=False
        )
    return tmp_path / "canonical"


# ── naming ───────────────────────────────────────────────────────────────────────────────


def test_names_follow_the_epic_naming_contract(names: seed.Names) -> None:
    assert names.database == "FLIP_TRUST_GSTT"
    assert names.omop_schema == "FLIP_TRUST_GSTT.OMOP"
    assert names.seed_stage == "@FLIP_TRUST_GSTT.STAGES.SEED"
    assert names.loader_role == "FLIP_GSTT_OMOP_LOADER"
    assert names.warehouse == "FLIP_GSTT_WH"


@pytest.mark.parametrize("code", ["", "a'; DROP DATABASE X; --", "GS TT", "9GSTT", "GSTT.OMOP"])
def test_a_code_that_is_not_an_identifier_is_refused(code: str) -> None:
    with pytest.raises(seed.SnowflakeSeedError, match="not usable as a Snowflake identifier"):
        seed.names_for(code)


def test_the_session_pins_the_loader_role(names: seed.Names) -> None:
    """A connection whose default role is broader must still seed as the loader."""
    assert seed.sql_session_preamble(names)[0] == "USE ROLE FLIP_GSTT_OMOP_LOADER;"


# ── --if-needed: seed only an empty OMOP (FLIP#2111) ──────────────────────────────────────


def _missing(table: str):
    def fail(*_args, **_kwargs):
        raise seed.SnowflakeSeedError(
            "`snow sql` failed with exit code 1: 002003 (42S02): SQL compilation error: "
            f"Object 'FLIP_TRUST_GSTT.OMOP.{table}' does not exist or not authorized."
        )

    return fail


def test_imaging_rows_are_what_the_check_reads(names: seed.Names) -> None:
    """Image rows, not persons, as on Postgres: Synthea adds persons with no imaging."""
    assert seed.sql_holds_imaging(names) == "SELECT 1 AS loaded FROM FLIP_TRUST_GSTT.OMOP.IMAGE_OCCURRENCE LIMIT 1;"


def test_an_omop_with_imaging_rows_holds_data(monkeypatch: pytest.MonkeyPatch, names: seed.Names) -> None:
    monkeypatch.setattr(seed, "run_sql", lambda *args, **kwargs: [{"LOADED": 1}])
    assert seed.holds_imaging(names, "flip-test", "snow") is True
    monkeypatch.setattr(seed, "run_sql", lambda *args, **kwargs: [])
    assert seed.holds_imaging(names, "flip-test", "snow") is False


def test_a_fresh_trust_without_the_table_holds_none(monkeypatch: pytest.MonkeyPatch, names: seed.Names) -> None:
    monkeypatch.setattr(seed, "run_sql", _missing("IMAGE_OCCURRENCE"))
    assert seed.holds_imaging(names, "flip-test", "snow") is False


def test_any_other_failure_of_the_check_raises(monkeypatch: pytest.MonkeyPatch, names: seed.Names) -> None:
    monkeypatch.setattr(seed, "run_sql", _missing("CONCEPT"))
    with pytest.raises(seed.SnowflakeSeedError):
        seed.holds_imaging(names, "flip-test", "snow")


# ── DDL and loading SQL ──────────────────────────────────────────────────────────────────


def test_create_tables_is_the_translated_ddl_alone(names: seed.Names) -> None:
    ddl = "CREATE TABLE IF NOT EXISTS PERSON (person_id SERIAL PRIMARY KEY NOT NULL);"
    statements = seed.sql_create_tables(names, ddl)
    assert len(statements) == 1
    assert statements[0].startswith("CREATE TABLE IF NOT EXISTS FLIP_TRUST_GSTT.OMOP.PERSON")


def test_put_is_absolute_and_overwrites(tmp_path: Path) -> None:
    statement = seed.sql_put(tmp_path / "person.csv", "@DB.STAGES.SEED/cxr_project")
    assert statement.startswith(f"PUT 'file://{tmp_path}/person.csv' '@DB.STAGES.SEED/cxr_project'")
    assert "AUTO_COMPRESS = TRUE" in statement
    assert "OVERWRITE = TRUE" in statement


def test_put_refuses_a_relative_path() -> None:
    with pytest.raises(seed.SnowflakeSeedError, match="absolute path"):
        seed.sql_put(Path("person.csv"), "@S")


def test_copy_into_binds_columns_by_position(names: seed.Names) -> None:
    statement = seed.sql_copy_into(names, "person", ["person_id", "year_of_birth"], "@S/person.csv.gz")
    assert "COPY INTO FLIP_TRUST_GSTT.OMOP.PERSON (person_id, year_of_birth)" in statement
    assert "SELECT $1, $2 FROM '@S/person.csv.gz'" in statement
    assert "SKIP_HEADER = 1" in statement
    assert "ON_ERROR = ABORT_STATEMENT" in statement


def test_copy_into_forces_a_reload(names: seed.Names) -> None:
    """Snowflake remembers a staged file it has loaded and skips it: after the scoped delete a
    re-seed loaded nothing and left the tables empty (found on the live account)."""
    assert "FORCE = TRUE" in seed.sql_copy_into(names, "person", ["person_id"], "@S/person.csv.gz")


def test_copy_into_refuses_an_unsafe_column_name(names: seed.Names) -> None:
    with pytest.raises(seed.SnowflakeSeedError, match="Unsafe SQL identifier"):
        seed.sql_copy_into(names, "person", ["person_id); DROP TABLE PERSON; --"], "@S/x.gz")


def test_deletes_are_person_scoped_reverse_order_and_chunked(names: seed.Names) -> None:
    statements = seed.sql_delete_person_rows(names, list(range(1, 2501)))
    assert statements[0].startswith("DELETE FROM FLIP_TRUST_GSTT.OMOP.OBSERVATION")
    assert statements[-1].startswith("DELETE FROM FLIP_TRUST_GSTT.OMOP.PERSON")
    assert all("WHERE person_id IN (" in statement for statement in statements)
    # 7 canonical tables x ceil(2500/1000) chunks
    assert len(statements) == 7 * 3


def test_no_ids_means_no_delete(names: seed.Names) -> None:
    assert seed.sql_delete_person_rows(names, []) == []


def test_delete_refuses_a_non_integer_id(names: seed.Names) -> None:
    with pytest.raises(ValueError, match="invalid literal for int"):
        seed.sql_delete_person_rows(names, ["1); DROP TABLE PERSON; --"])  # type: ignore[list-item]


# ── partition filtering ──────────────────────────────────────────────────────────────────


def test_only_this_trusts_partition_is_staged(canonical: Path, tmp_path: Path) -> None:
    written = seed.write_project_csvs(canonical, "cxr_project", tmp_path / "stage", 2, 1, "source_trust")
    person = pd.read_csv(written["person"])
    assert person["person_id"].tolist() == [1, 2]
    # The provenance column never reaches the trust.
    assert "source_trust" not in person.columns


def test_the_other_partition_gets_the_other_rows(canonical: Path, tmp_path: Path) -> None:
    written = seed.write_project_csvs(canonical, "cxr_project", tmp_path / "stage", 2, 2, "source_trust")
    assert pd.read_csv(written["person"])["person_id"].tolist() == [3, 4]


def test_tables_with_no_rows_for_this_trust_are_not_staged(canonical: Path, tmp_path: Path) -> None:
    """An empty slice has nothing to COPY; staging it would COPY a header-only file."""
    pd.DataFrame({"person_id": [1, 3], "visit_occurrence_id": [10, 30]}).to_csv(
        canonical / "cxr_project" / "visit_occurrence.csv", index=False
    )
    pd.DataFrame({"person_id": [1, 2, 3, 4], "gender_concept_id": [0, 0, 0, 0]}).to_csv(
        canonical / "cxr_project" / "person.csv", index=False
    )
    # modulo: trust 2 takes person_id % 2 == 1, and every visit row here is odd-keyed.
    written = seed.write_project_csvs(canonical, "cxr_project", tmp_path / "stage", 2, 1, "modulo")
    assert "visit_occurrence" not in written
    assert "person" in written


def test_optional_tables_absent_from_a_project_are_skipped(canonical: Path, tmp_path: Path) -> None:
    """cxr publishes observations but no measurements, and the other way round for spleen."""
    written = seed.write_project_csvs(canonical, "cxr_project", tmp_path / "stage", 2, 1, "source_trust")
    assert "measurement" not in written
    assert "observation" not in written


def test_a_missing_required_table_fails_before_anything_is_staged(canonical: Path, tmp_path: Path) -> None:
    (canonical / "cxr_project" / "person.csv").unlink()
    with pytest.raises(FileNotFoundError, match="Required table CSV not found"):
        seed.write_project_csvs(canonical, "cxr_project", tmp_path / "stage", 2, 1, "source_trust")


def test_load_statements_put_everything_before_copying_anything(
    canonical: Path, tmp_path: Path, names: seed.Names
) -> None:
    written = seed.write_project_csvs(canonical, "cxr_project", tmp_path / "stage", 2, 1, "source_trust")
    statements = seed.load_statements(names, written, "@FLIP_TRUST_GSTT.STAGES.SEED/cxr_project")
    kinds = [statement.split()[0] for statement in statements]
    assert kinds == ["PUT"] * len(written) + ["COPY"] * len(written)
    assert statements[len(written)].startswith("COPY INTO FLIP_TRUST_GSTT.OMOP.PERSON")
    assert "person.csv.gz" in statements[len(written)]


# ── whole-run composition ────────────────────────────────────────────────────────────────


def _seed_statements(canonical: Path, tmp_path: Path, names: seed.Names, **overrides: object) -> list[str]:
    kwargs: dict[str, object] = dict(
        names=names,
        data_dir=canonical,
        projects=["cxr_project"],
        staging_dir=tmp_path / "stage",
        num_trusts=2,
        source_trust=1,
        partition="source_trust",
        ddl_sql="CREATE TABLE IF NOT EXISTS PERSON (person_id SERIAL PRIMARY KEY NOT NULL);",
        vocab_dir=None,
        load_vocab=False,
    )
    kwargs.update(overrides)
    return seed.build_seed_statements(**kwargs)  # type: ignore[arg-type]


def test_a_seed_run_is_ordered_ddl_clean_load(canonical: Path, tmp_path: Path, names: seed.Names) -> None:
    statements = _seed_statements(canonical, tmp_path, names)
    joined = "\n".join(statements)
    create = joined.index("CREATE TABLE IF NOT EXISTS FLIP_TRUST_GSTT.OMOP.PERSON")
    delete = joined.index("DELETE FROM FLIP_TRUST_GSTT.OMOP.PERSON WHERE person_id IN")
    copy = joined.index("COPY INTO FLIP_TRUST_GSTT.OMOP.PERSON")
    assert create < delete < copy


def test_a_projects_delete_and_load_are_one_transaction(canonical: Path, tmp_path: Path, names: seed.Names) -> None:
    """A load that fails after the delete must not leave the project's tables empty."""
    statements = _seed_statements(canonical, tmp_path, names)
    begin, commit = statements.index("BEGIN TRANSACTION;"), statements.index("COMMIT;")
    inside = statements[begin + 1 : commit]

    assert {s.split()[0] for s in inside} == {"DELETE", "COPY"}
    assert sum(s.startswith("DELETE") for s in inside) == 7
    assert sum(s.startswith("COPY") for s in inside) == 5
    # PUT is a file upload and DDL commits implicitly, so both stay ahead of the transaction.
    assert all(not s.startswith(("PUT", "CREATE")) for s in statements[begin:])
    assert statements.count("BEGIN TRANSACTION;") == statements.count("COMMIT;") == 1


def test_the_scoped_clean_uses_the_whole_projects_person_ids(
    canonical: Path, tmp_path: Path, names: seed.Names
) -> None:
    """Both partitions' ids, as on Postgres: a database holding another trust's rows for this
    project is put right rather than accumulating them."""
    statements = _seed_statements(canonical, tmp_path, names)
    delete = next(s for s in statements if s.startswith("DELETE FROM FLIP_TRUST_GSTT.OMOP.PERSON"))
    assert "IN (1, 2, 3, 4)" in delete


def test_clean_false_loads_without_deleting(canonical: Path, tmp_path: Path, names: seed.Names) -> None:
    statements = _seed_statements(canonical, tmp_path, names, clean=False)
    assert not any(s.startswith("DELETE FROM FLIP_TRUST_GSTT.OMOP.PERSON WHERE") for s in statements)


def test_a_present_vocabulary_is_skipped_with_the_reason_in_the_sql(
    canonical: Path, tmp_path: Path, names: seed.Names
) -> None:
    statements = _seed_statements(canonical, tmp_path, names, load_vocab=False)
    assert any("DICOM vocabulary already present" in s and "duplicate" in s for s in statements)


def test_loading_the_vocabulary_without_a_directory_is_refused(
    canonical: Path, tmp_path: Path, names: seed.Names
) -> None:
    with pytest.raises(seed.SnowflakeSeedError, match="needs --vocab-dir"):
        _seed_statements(canonical, tmp_path, names, load_vocab=True)


# ── verifying what was loaded ────────────────────────────────────────────────────────────


def _staged(canonical: Path, tmp_path: Path) -> Path:
    staging = tmp_path / "stage"
    seed.write_project_csvs(canonical, "cxr_project", staging, 2, 1, "source_trust")
    return staging


def _counts(**loaded: int) -> list[dict[str, object]]:
    return [{"TABLE_NAME": table.upper(), "LOADED": count} for table, count in loaded.items()]


def test_the_count_query_is_scoped_to_the_projects_persons(names: seed.Names) -> None:
    query = seed.sql_count_person_rows(names, list(range(1, 1502)), ["person", "visit_occurrence"])
    assert query.count("UNION ALL") == 1
    assert "SELECT 'PERSON' AS table_name, COUNT(*) AS loaded FROM FLIP_TRUST_GSTT.OMOP.PERSON" in query
    # Chunked like the deletes: 1501 ids are two IN lists per table.
    assert query.count("person_id IN (") == 4


def test_matching_row_counts_pass(
    canonical: Path, tmp_path: Path, names: seed.Names, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = _staged(canonical, tmp_path)
    full = _counts(person=2, visit_occurrence=1, procedure_occurrence=1, image_occurrence=1, image_feature=1)
    monkeypatch.setattr(seed, "run_sql", lambda *args, **kwargs: full)
    seed.verify_loaded(names, canonical, ["cxr_project"], staging, "flip-test", "snow")


def test_a_load_that_loaded_nothing_is_an_error(
    canonical: Path, tmp_path: Path, names: seed.Names, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``COPY INTO`` answers "0 files processed" with exit code 0, so only a count can tell."""
    staging = _staged(canonical, tmp_path)
    empty = _counts(person=0, visit_occurrence=0, procedure_occurrence=0, image_occurrence=0, image_feature=0)
    monkeypatch.setattr(seed, "run_sql", lambda *args, **kwargs: empty)
    with pytest.raises(seed.SnowflakeSeedError, match=r"cxr_project.*person: 0 of 2"):
        seed.verify_loaded(names, canonical, ["cxr_project"], staging, "flip-test", "snow")


def test_a_table_missing_from_the_count_is_an_error(
    canonical: Path, tmp_path: Path, names: seed.Names, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = _staged(canonical, tmp_path)
    monkeypatch.setattr(seed, "run_sql", lambda *args, **kwargs: _counts(person=2))
    with pytest.raises(seed.SnowflakeSeedError, match="visit_occurrence: 0 of 1"):
        seed.verify_loaded(names, canonical, ["cxr_project"], staging, "flip-test", "snow")


def _fake_snowflake(monkeypatch: pytest.MonkeyPatch, loaded: list[dict[str, object]]) -> list[str]:
    """Replace ``run_sql`` for a ``main`` run; the returned list fills with every script sent."""
    scripts: list[str] = []

    def fake_run_sql(statements: list[str], **kwargs: object) -> list[dict[str, object]] | None:
        script = "\n".join(statements)
        scripts.append(script)
        if "SELECT 1 AS loaded" in script:
            return [{"LOADED": 1}]  # the DICOM vocabulary is already there
        return loaded if "COUNT(*) AS loaded" in script else None

    monkeypatch.setattr(seed, "run_sql", fake_run_sql)
    monkeypatch.setattr(seed, "validate_data_dir", lambda *args: None)
    monkeypatch.setattr(seed, "read_ddl", lambda _path: "CREATE TABLE IF NOT EXISTS PERSON (person_id INT);")
    return scripts


def _main_args(canonical: Path) -> list[str]:
    return [
        "--code",
        "GSTT",
        "--connection",
        "review",
        "--projects",
        "cxr_project",
        "--data-dir",
        str(canonical),
    ]


def test_the_rows_are_counted_after_the_load(canonical: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    full = _counts(person=2, visit_occurrence=1, procedure_occurrence=1, image_occurrence=1, image_feature=1)
    scripts = _fake_snowflake(monkeypatch, full)

    assert seed.main(_main_args(canonical)) == 0

    assert ["copy" if "COPY INTO" in s else "count" if "COUNT(*)" in s else "?" for s in scripts[1:]] == [
        "copy",
        "count",
    ]


def test_an_empty_load_fails_the_run_and_says_to_re_seed(canonical: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--if-needed would skip the half-loaded OMOP next time, so the error names the explicit seed."""
    _fake_snowflake(monkeypatch, _counts(person=0))

    with pytest.raises(seed.SnowflakeSeedError, match="person: 0 of 2.*Re-run seed-omop-snowflake"):
        seed.main(_main_args(canonical))


def test_if_needed_leaves_an_omop_that_holds_data(canonical: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scripts = _fake_snowflake(monkeypatch, [])
    monkeypatch.setattr(seed, "holds_imaging", lambda *args: True)

    assert seed.main([*_main_args(canonical), "--if-needed"]) == 0

    assert scripts == [], "nothing is staged, loaded or counted"


def test_if_needed_seeds_an_empty_omop(canonical: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    full = _counts(person=2, visit_occurrence=1, procedure_occurrence=1, image_occurrence=1, image_feature=1)
    scripts = _fake_snowflake(monkeypatch, full)
    monkeypatch.setattr(seed, "holds_imaging", lambda *args: False)

    assert seed.main([*_main_args(canonical), "--if-needed"]) == 0

    assert any("COPY INTO" in s for s in scripts)


# ── unseed ───────────────────────────────────────────────────────────────────────────────


def test_unseed_deletes_by_person_and_loads_nothing(canonical: Path, names: seed.Names) -> None:
    statements = seed.build_unseed_statements(names, canonical, ["cxr_project"])
    assert not any(s.startswith(("COPY", "PUT")) for s in statements)
    assert any(
        s.startswith("DELETE FROM FLIP_TRUST_GSTT.OMOP.PERSON WHERE person_id IN (1, 2, 3, 4)") for s in statements
    )


def test_unseed_checks_every_project_before_deleting_any(canonical: Path, names: seed.Names) -> None:
    with pytest.raises(FileNotFoundError, match="Cannot unseed spleen_project"):
        seed.build_unseed_statements(names, canonical, ["cxr_project", "spleen_project"])


# ── the snow CLI boundary ────────────────────────────────────────────────────────────────


def test_dry_run_prints_and_executes_nothing(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("a dry run must not start a process")

    monkeypatch.setattr(subprocess, "run", explode)
    assert seed.run_sql(["SELECT 1;"], connection="flip-test", dry_run=True) is None
    assert "SELECT 1;" in capsys.readouterr().out


def test_a_missing_snow_cli_is_reported_not_crashed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(seed.shutil, "which", lambda _: None)
    with pytest.raises(seed.SnowflakeSeedError, match="is not on PATH"):
        seed.run_sql(["SELECT 1;"], connection="flip-test")


def test_run_sql_pipes_one_script_to_one_process(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        seen["input"] = kwargs["input"]
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(seed.shutil, "which", lambda _: "/usr/bin/snow")
    monkeypatch.setattr(subprocess, "run", fake_run)
    seed.run_sql(["USE ROLE R;", "SELECT 1;"], connection="flip-test")
    assert seen["command"] == ["snow", "sql", "--stdin", "--enable-templating", "NONE", "--connection", "flip-test"]
    assert seen["input"] == "USE ROLE R;\nSELECT 1;\n"


def test_a_failing_cli_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(seed.shutil, "which", lambda _: "/usr/bin/snow")
    monkeypatch.setattr(
        subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 2, "", "boom")
    )
    with pytest.raises(seed.SnowflakeSeedError, match="exit code 2"):
        seed.run_sql(["SELECT 1;"], connection="flip-test", capture=True)


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("", []),
        ("not json", []),
        ('[{"PROJECTS": "cxr_project"}]', [{"PROJECTS": "cxr_project"}]),
        ('[[{"a": 1}], [{"PROJECTS": "cxr_project"}]]', [{"PROJECTS": "cxr_project"}]),
    ],
    ids=["empty", "garbage", "single statement", "multi statement takes the last"],
)
def test_snow_json_parsing(stdout: str, expected: list[dict[str, object]]) -> None:
    assert seed.parse_snow_json(stdout) == expected


def test_a_dry_run_plans_the_vocabulary_load(monkeypatch: pytest.MonkeyPatch, names: seed.Names) -> None:
    """Nothing is known on a dry run, so the printed plan shows the full seed."""
    monkeypatch.setattr(seed, "run_sql", lambda *args, **kwargs: None)
    assert seed.dicom_vocab_present(names, "flip-test", "snow", dry_run=True) is False


def test_a_fresh_trust_without_a_concept_table_counts_as_vocabulary_absent(
    monkeypatch: pytest.MonkeyPatch, names: seed.Names
) -> None:
    """The presence check runs before the seed creates the tables; on a fresh trust Snowflake
    answers 002003 for OMOP.CONCEPT, which means "load it", not "fail" (found on the live account)."""

    def missing_table(*_args, **_kwargs):
        raise seed.SnowflakeSeedError(
            "`snow sql` failed with exit code 1: 002003 (42S02): SQL compilation error: "
            "Object 'FLIP_TRUST_GSTT.OMOP.CONCEPT' does not exist or not authorized."
        )

    monkeypatch.setattr(seed, "run_sql", missing_table)
    assert seed.dicom_vocab_present(names, "flip-test", "snow", dry_run=False) is False


def test_any_other_snow_failure_in_the_presence_check_still_raises(
    monkeypatch: pytest.MonkeyPatch, names: seed.Names
) -> None:
    def other_error(*_args, **_kwargs):
        raise seed.SnowflakeSeedError("`snow sql` failed with exit code 1: 390201 warehouse does not exist")

    monkeypatch.setattr(seed, "run_sql", other_error)
    with pytest.raises(seed.SnowflakeSeedError):
        seed.dicom_vocab_present(names, "flip-test", "snow", dry_run=False)


def test_snow_runs_with_templating_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """snow rewrites & in SQL unless templating is off, which corrupts any literal holding one."""
    seen: list[list[str]] = []

    class Done:
        returncode = 0
        stdout = "[]"
        stderr = ""

    def fake_run(command: list[str], **_kw: object) -> Done:
        seen.append(command)
        return Done()

    monkeypatch.setattr(seed.shutil, "which", lambda _name: "/usr/bin/snow")
    monkeypatch.setattr(seed.subprocess, "run", fake_run)
    seed.run_sql(["SELECT 1;"], connection="flip-test")
    assert seen[0][seen[0].index("--enable-templating") + 1] == "NONE"
