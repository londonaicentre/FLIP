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
"""Unit tests for the DICOM vocabulary loader helpers (no database required)."""

import zipfile
from unittest.mock import MagicMock

import pandas as pd
import pytest

from omop_db_tools.load_dicom_vocab import REQUIRED_VOCAB_FILES, ensure_vocab_dir, safe_insert


class TestSafeInsert:
    def _engine(self, rowcounts):
        engine = MagicMock()
        conn = engine.begin.return_value.__enter__.return_value
        conn.execute.side_effect = [MagicMock(rowcount=count) for count in rowcounts]
        return engine, conn

    def test_emits_on_conflict_do_nothing_and_counts(self, capsys):
        engine, conn = self._engine([1, 0])
        df = pd.DataFrame({"concept_id": [1, 2], "concept_name": ["a", "b"]})

        safe_insert("CONCEPT", df, engine)

        assert conn.execute.call_count == 2
        statement = str(conn.execute.call_args_list[0].args[0])
        assert "ON CONFLICT DO NOTHING" in statement
        assert "INSERT INTO omop.CONCEPT (concept_id, concept_name)" in statement
        assert "inserted=1 skipped=1" in capsys.readouterr().out

    def test_unsafe_table_rejected(self):
        engine, _ = self._engine([])
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            safe_insert("CONCEPT; DROP TABLE x", pd.DataFrame({"a": [1]}), engine)

    def test_unsafe_column_rejected(self):
        engine, _ = self._engine([])
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            safe_insert("CONCEPT", pd.DataFrame({"Unnamed: 0": [1]}), engine)


class TestEnsureVocabDir:
    def _write_zip(self, tmp_path, names):
        bundle = tmp_path / "bundle"
        with zipfile.ZipFile(tmp_path / "bundle.zip", "w") as zip_ref:
            for name in names:
                zip_ref.writestr(f"bundle/{name}", "concept_id\n1\n")
        return bundle

    def test_extracts_zip_when_dir_absent(self, tmp_path):
        bundle = self._write_zip(tmp_path, REQUIRED_VOCAB_FILES)

        ensure_vocab_dir(bundle)

        for name in REQUIRED_VOCAB_FILES:
            assert (bundle / name).is_file()

    def test_existing_complete_dir_accepted(self, tmp_path):
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        for name in REQUIRED_VOCAB_FILES:
            (bundle / name).write_text("concept_id\n1\n")

        ensure_vocab_dir(bundle)

    def test_incomplete_dir_rejected_with_remediation(self, tmp_path):
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        (bundle / REQUIRED_VOCAB_FILES[0]).write_text("concept_id\n1\n")

        with pytest.raises(FileNotFoundError, match="fetch-vocab-dicom"):
            ensure_vocab_dir(bundle)

    def test_neither_dir_nor_zip_rejected(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="fetch-vocab-dicom"):
            ensure_vocab_dir(tmp_path / "bundle")
