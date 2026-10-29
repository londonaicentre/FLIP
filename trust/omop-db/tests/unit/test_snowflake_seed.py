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
"""Snowflake OMOP seeding: SQL generation, marker logic, partition filtering, unseed (FLIP#1580).

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
    assert names.marker_table == "FLIP_TRUST_GSTT.OMOP._FLIP_SEED_MARKER"


@pytest.mark.parametrize("code", ["", "a'; DROP DATABASE X; --", "GS TT", "9GSTT", "GSTT.OMOP"])
def test_a_code_that_is_not_an_identifier_is_refused(code: str) -> None:
    with pytest.raises(seed.SnowflakeSeedError, match="not usable as a Snowflake identifier"):
        seed.names_for(code)


def test_the_session_pins_the_loader_role(names: seed.Names) -> None:
    """A connection whose default role is broader must still seed as the loader."""
    assert seed.sql_session_preamble(names)[0] == "USE ROLE FLIP_GSTT_OMOP_LOADER;"


# ── marker ───────────────────────────────────────────────────────────────────────────────


def test_marker_text_matches_the_postgres_seeded_file() -> None:
    marker = seed.Marker(projects=("cxr_project", "spleen_project"), source_trust=2, provenance="version=20261001")
    assert marker.text() == "projects=cxr_project spleen_project\nsource_trust=2\nversion=20261001\n"


def test_provenance_prefers_a_local_tree_over_a_tag() -> None:
    assert seed.provenance_for("20261001", None) == "version=20261001"
    assert seed.provenance_for("20261001", "/abs/canonical") == "source=/abs/canonical"
    with pytest.raises(seed.SnowflakeSeedError):
        seed.provenance_for(None, None)


def test_a_matching_marker_is_a_no_op() -> None:
    wanted = seed.Marker(projects=("cxr_project",), source_trust=1, provenance="version=20261001")
    assert seed.projects_to_reseed(wanted, wanted) == []


@pytest.mark.parametrize(
    "current",
    [
        None,
        seed.Marker(projects=("cxr_project",), source_trust=1, provenance="version=20260911"),
        seed.Marker(projects=("cxr_project",), source_trust=2, provenance="version=20261001"),
        seed.Marker(projects=("spleen_project",), source_trust=1, provenance="version=20261001"),
    ],
    ids=["no marker", "version bump", "repartition", "different projects"],
)
def test_a_differing_marker_reseeds_every_listed_project(current: seed.Marker | None) -> None:
    wanted = seed.Marker(projects=("cxr_project", "spleen_project"), source_trust=1, provenance="version=20261001")
    assert seed.projects_to_reseed(wanted, current) == ["cxr_project", "spleen_project"]


def test_the_marker_table_holds_exactly_one_row(names: seed.Names) -> None:
    marker = seed.Marker(projects=("cxr_project",), source_trust=1, provenance="version=20261001")
    statements = seed.sql_write_marker(names, marker)
    assert statements[0] == "DELETE FROM FLIP_TRUST_GSTT.OMOP._FLIP_SEED_MARKER;"
    assert statements[1].startswith("INSERT INTO FLIP_TRUST_GSTT.OMOP._FLIP_SEED_MARKER")
    assert "'cxr_project', 1, 'version=20261001'" in statements[1]


def test_marker_values_are_escaped(names: seed.Names) -> None:
    marker = seed.Marker(projects=("it's",), source_trust=1, provenance="source=/a'b")
    assert "'it''s'" in seed.sql_write_marker(names, marker)[1]
    assert "'source=/a''b'" in seed.sql_write_marker(names, marker)[1]


# ── DDL and loading SQL ──────────────────────────────────────────────────────────────────


def test_create_tables_includes_the_marker_table(names: seed.Names) -> None:
    ddl = "CREATE TABLE IF NOT EXISTS PERSON (person_id SERIAL PRIMARY KEY NOT NULL);"
    statements = seed.sql_create_tables(names, ddl)
    assert statements[0].startswith("CREATE TABLE IF NOT EXISTS FLIP_TRUST_GSTT.OMOP.PERSON")
    assert statements[-1] == seed.sql_create_marker_table(names)


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
        marker=seed.Marker(projects=("cxr_project",), source_trust=1, provenance="version=20261001"),
        ddl_sql="CREATE TABLE IF NOT EXISTS PERSON (person_id SERIAL PRIMARY KEY NOT NULL);",
        vocab_dir=None,
        load_vocab=False,
    )
    kwargs.update(overrides)
    return seed.build_seed_statements(**kwargs)  # type: ignore[arg-type]


def test_a_seed_run_is_ordered_ddl_clean_load_marker(canonical: Path, tmp_path: Path, names: seed.Names) -> None:
    statements = _seed_statements(canonical, tmp_path, names)
    joined = "\n".join(statements)
    create = joined.index("CREATE TABLE IF NOT EXISTS FLIP_TRUST_GSTT.OMOP.PERSON")
    delete = joined.index("DELETE FROM FLIP_TRUST_GSTT.OMOP.PERSON WHERE person_id IN")
    copy = joined.index("COPY INTO FLIP_TRUST_GSTT.OMOP.PERSON")
    marker = joined.index("INSERT INTO FLIP_TRUST_GSTT.OMOP._FLIP_SEED_MARKER")
    assert create < delete < copy < marker


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


# ── unseed ───────────────────────────────────────────────────────────────────────────────


def test_unseed_deletes_by_person_and_loads_nothing(canonical: Path, names: seed.Names) -> None:
    statements = seed.build_unseed_statements(names, canonical, ["cxr_project"])
    assert not any(s.startswith(("COPY", "PUT")) for s in statements)
    assert any(
        s.startswith("DELETE FROM FLIP_TRUST_GSTT.OMOP.PERSON WHERE person_id IN (1, 2, 3, 4)") for s in statements
    )


def test_unseed_leaves_the_marker_alone(canonical: Path, names: seed.Names) -> None:
    statements = seed.build_unseed_statements(names, canonical, ["cxr_project"])
    assert not any("_FLIP_SEED_MARKER" in s for s in statements)


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
    assert seen["command"] == ["snow", "sql", "--stdin", "--connection", "flip-test"]
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


def test_read_marker_tolerates_either_case_from_the_cli(monkeypatch: pytest.MonkeyPatch, names: seed.Names) -> None:
    monkeypatch.setattr(
        seed,
        "run_sql",
        lambda *args, **kwargs: [
            {"PROJECTS": "cxr_project spleen_project", "SOURCE_TRUST": "2", "PROVENANCE": "version=x"}
        ],
    )
    marker = seed.read_marker(names, "flip-test", "snow", dry_run=False)
    assert marker == seed.Marker(("cxr_project", "spleen_project"), 2, "version=x")


def test_read_marker_is_none_on_an_empty_table(monkeypatch: pytest.MonkeyPatch, names: seed.Names) -> None:
    monkeypatch.setattr(seed, "run_sql", lambda *args, **kwargs: [])
    assert seed.read_marker(names, "flip-test", "snow", dry_run=False) is None


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
