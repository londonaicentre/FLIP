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
from omop_db_tools.import_tables import load_project, validate_data_dir, validate_identifier


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

        with pytest.raises(FileNotFoundError, match="procedure_occurrence"):
            validate_data_dir(tmp_path, ["cxr_project"])


class TestLoadProject:
    def _record_to_sql(self, monkeypatch):
        loaded = []

        def fake_to_sql(self_df, name, engine, **kwargs):
            loaded.append((name, len(self_df)))

        monkeypatch.setattr(pd.DataFrame, "to_sql", fake_to_sql)
        return loaded

    def test_optional_missing_skipped_and_rest_loaded(self, tmp_path, monkeypatch):
        required = [table for table in CANONICAL_TABLES if table not in {"measurement", "observation"}]
        _write_project(tmp_path, "cxr_project", required)
        loaded = self._record_to_sql(monkeypatch)

        load_project(None, tmp_path, "cxr_project", num_trusts=1, trust_index=1, partition="legacy")

        assert [name for name, _ in loaded] == required

    def test_headers_only_required_table_rejected(self, tmp_path, monkeypatch):
        required = [table for table in CANONICAL_TABLES if table not in {"measurement", "observation"}]
        project_dir = _write_project(tmp_path, "cxr_project", required)
        (project_dir / "person.csv").write_text(f"person_id,{SOURCE_TRUST_COLUMN}\n")
        self._record_to_sql(monkeypatch)

        with pytest.raises(ValueError, match="headers but no rows"):
            load_project(None, tmp_path, "cxr_project", num_trusts=1, trust_index=1, partition="legacy")

    def test_missing_required_table_rejected(self, tmp_path, monkeypatch):
        _write_project(tmp_path, "cxr_project", ["person"])
        self._record_to_sql(monkeypatch)

        with pytest.raises(FileNotFoundError, match="procedure_occurrence"):
            load_project(None, tmp_path, "cxr_project", num_trusts=1, trust_index=1, partition="legacy")
