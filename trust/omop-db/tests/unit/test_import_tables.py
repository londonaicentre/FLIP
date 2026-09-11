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
"""Unit tests for the table-import helpers (no database required)."""

import pandas as pd
import pytest

from omop_db_tools.dataset import CANONICAL_TABLES, SOURCE_TRUST_COLUMN
from omop_db_tools.import_tables import (
    clean_project,
    load_project,
    project_person_ids,
    validate_data_dir,
    validate_identifier,
)


class _FakeResult:
    rowcount = 0


class _FakeConn:
    """Records every statement executed inside the load transaction."""

    def __init__(self):
        self.executed: list[tuple[str, dict]] = []

    def execute(self, statement, params=None):
        self.executed.append((str(statement), params or {}))
        return _FakeResult()


class _FakeEngine:
    """Stands in for a SQLAlchemy Engine: ``begin()`` yields one recording connection."""

    def __init__(self):
        self.conn = _FakeConn()
        self.begun = 0

    def begin(self):
        engine = self

        class _Txn:
            def __enter__(self_txn):
                engine.begun += 1
                return engine.conn

            def __exit__(self_txn, *exc):
                return False

        return _Txn()


def _write_project(data_dir, project, tables, rows=2):
    project_dir = data_dir / project
    project_dir.mkdir(parents=True, exist_ok=True)
    for table in tables:
        pd.DataFrame(
            {
                "person_id": range(1, rows + 1),
                SOURCE_TRUST_COLUMN: [1] * rows,
            }
        ).to_csv(project_dir / f"{table}.csv", index=False)
    return project_dir


class TestValidateIdentifier:
    @pytest.mark.parametrize("name", ["person", "image_occurrence", "CONCEPT", "_private"])
    def test_plain_identifiers_accepted(self, name):
        assert validate_identifier(name) == name

    @pytest.mark.parametrize("name", ["person; DROP TABLE x", "Unnamed: 0", "a-b", "1st", ""])
    def test_unsafe_identifiers_rejected(self, name):
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            validate_identifier(name)


class TestValidateDataDir:
    def test_all_required_present_passes(self, tmp_path):
        required = [table for table in CANONICAL_TABLES if table not in {"measurement", "observation"}]
        _write_project(tmp_path, "cxr_project", required)

        validate_data_dir(tmp_path, ["cxr_project"])

    def test_missing_required_table_rejected(self, tmp_path):
        _write_project(tmp_path, "cxr_project", ["person"])

        # visit_occurrence is the first required table after person in FK-safe order
        with pytest.raises(FileNotFoundError, match="visit_occurrence"):
            validate_data_dir(tmp_path, ["cxr_project"])


class TestProjectPersonIds:
    def test_reads_every_person_across_trusts(self, tmp_path):
        project_dir = tmp_path / "cxr_project"
        project_dir.mkdir()
        pd.DataFrame({"person_id": [10, 20, 30], SOURCE_TRUST_COLUMN: [1, 2, 1], "extra": "x"}).to_csv(
            project_dir / "person.csv", index=False
        )

        assert project_person_ids(tmp_path, "cxr_project") == [10, 20, 30]


class TestCleanProject:
    def test_deletes_from_every_table_in_reverse_fk_safe_order_scoped_to_the_ids(self):
        conn = _FakeConn()

        clean_project(conn, [10, 20])

        tables = [sql.split("omop.")[1].split(" ")[0] for sql, _ in conn.executed]
        assert tables == list(reversed(CANONICAL_TABLES))
        for sql, params in conn.executed:
            assert "WHERE person_id = ANY(:ids)" in sql, "every delete must be scoped, never a bare DELETE"
            assert params == {"ids": [10, 20]}


class TestLoadProject:
    def _record_to_sql(self, monkeypatch):
        loaded = []

        def fake_to_sql(self_df, name, con, **kwargs):
            loaded.append((name, len(self_df), con))

        monkeypatch.setattr(pd.DataFrame, "to_sql", fake_to_sql)
        return loaded

    def test_optional_missing_skipped_and_rest_loaded(self, tmp_path, monkeypatch):
        required = [table for table in CANONICAL_TABLES if table not in {"measurement", "observation"}]
        _write_project(tmp_path, "cxr_project", required)
        loaded = self._record_to_sql(monkeypatch)

        load_project(_FakeEngine(), tmp_path, "cxr_project", num_trusts=1, trust_index=1, partition="source_trust")

        assert [name for name, _, _ in loaded] == required

    def test_loads_inside_one_transaction_on_the_same_connection(self, tmp_path, monkeypatch):
        """Clean and every insert share the transaction, so a mid-project failure leaves nothing behind."""
        required = [table for table in CANONICAL_TABLES if table not in {"measurement", "observation"}]
        _write_project(tmp_path, "cxr_project", required)
        loaded = self._record_to_sql(monkeypatch)
        engine = _FakeEngine()

        load_project(engine, tmp_path, "cxr_project", num_trusts=1, trust_index=1, partition="source_trust")

        assert engine.begun == 1
        assert {con for _, _, con in loaded} == {engine.conn}

    def test_clean_projects_scopes_the_delete_to_this_projects_persons(self, tmp_path, monkeypatch):
        """The seed path must never issue an unscoped DELETE: Synthea rows and other projects stay."""
        required = [table for table in CANONICAL_TABLES if table not in {"measurement", "observation"}]
        _write_project(tmp_path, "cxr_project", required, rows=3)
        self._record_to_sql(monkeypatch)
        engine = _FakeEngine()

        load_project(engine, tmp_path, "cxr_project", 1, 1, "source_trust", clean="projects")

        deletes = [(sql, params) for sql, params in engine.conn.executed if sql.startswith("DELETE")]
        assert len(deletes) == len(CANONICAL_TABLES)
        assert all(params == {"ids": [1, 2, 3]} for _, params in deletes)
        assert all("WHERE person_id = ANY(:ids)" in sql for sql, _ in deletes)

    def test_clean_all_skips_the_scoped_delete(self, tmp_path, monkeypatch):
        """``--clean all`` empties the tables up front via clean_tables; load_project must not delete again."""
        required = [table for table in CANONICAL_TABLES if table not in {"measurement", "observation"}]
        _write_project(tmp_path, "cxr_project", required)
        self._record_to_sql(monkeypatch)
        engine = _FakeEngine()

        load_project(engine, tmp_path, "cxr_project", 1, 1, "source_trust", clean="all")

        assert not [sql for sql, _ in engine.conn.executed if sql.startswith("DELETE")]

    def test_legacy_alias_still_loads(self, tmp_path, monkeypatch):
        required = [table for table in CANONICAL_TABLES if table not in {"measurement", "observation"}]
        _write_project(tmp_path, "cxr_project", required)
        loaded = self._record_to_sql(monkeypatch)

        load_project(_FakeEngine(), tmp_path, "cxr_project", num_trusts=1, trust_index=1, partition="legacy")

        assert [name for name, _, _ in loaded] == required

    def test_headers_only_required_table_rejected(self, tmp_path, monkeypatch):
        required = [table for table in CANONICAL_TABLES if table not in {"measurement", "observation"}]
        project_dir = _write_project(tmp_path, "cxr_project", required)
        (project_dir / "person.csv").write_text(f"person_id,{SOURCE_TRUST_COLUMN}\n")
        self._record_to_sql(monkeypatch)

        with pytest.raises(ValueError, match="headers but no rows"):
            load_project(_FakeEngine(), tmp_path, "cxr_project", 1, 1, "source_trust")

    def test_missing_required_table_rejected(self, tmp_path, monkeypatch):
        _write_project(tmp_path, "cxr_project", ["person"])
        self._record_to_sql(monkeypatch)

        with pytest.raises(FileNotFoundError, match="visit_occurrence"):
            load_project(_FakeEngine(), tmp_path, "cxr_project", 1, 1, "source_trust")
