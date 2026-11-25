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
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from omop_db_tools.load_dicom_vocab import (
    DICOM_VOCABULARY_CONCEPT_ID,
    REQUIRED_VOCAB_FILES,
    dicom_vocabulary_loaded,
    ensure_vocab_dir,
    load_concepts,
    load_relationships,
    load_vocabulary_metadata,
    main,
    safe_insert,
)


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


class TestVocabularyMetadata:
    """The scaffolding the DICOM vocabulary hangs off; its first concept is what the already-loaded guard probes."""

    def test_inserts_the_scaffolding_concepts_then_the_vocabulary_and_its_classes(self, monkeypatch):
        inserted = []
        monkeypatch.setattr(
            "omop_db_tools.load_dicom_vocab.safe_insert", lambda table, df, engine: inserted.append((table, df))
        )

        load_vocabulary_metadata(MagicMock())

        assert [table for table, _ in inserted] == ["CONCEPT", "VOCABULARY", "CONCEPT_CLASS"]
        concepts, vocabulary, classes = (df for _, df in inserted)
        assert concepts["concept_id"].tolist() == [DICOM_VOCABULARY_CONCEPT_ID, 2128000001, 2128000002]
        assert vocabulary["vocabulary_id"].tolist() == ["DICOM"]
        assert vocabulary["vocabulary_concept_id"].tolist() == [DICOM_VOCABULARY_CONCEPT_ID]
        assert dict(zip(classes["concept_class_id"], classes["concept_class_concept_id"], strict=True)) == {
            "DICOM Attributes": 2128000001,
            "DICOM Value Sets": 2128000002,
        }


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

    def test_zip_with_traversal_member_rejected(self, tmp_path):
        with zipfile.ZipFile(tmp_path / "bundle.zip", "w") as zip_ref:
            zip_ref.writestr("../evil.csv", "concept_id\n1\n")

        with pytest.raises(ValueError, match="Unsafe member path"):
            ensure_vocab_dir(tmp_path / "bundle")

        assert not (tmp_path.parent / "evil.csv").exists()


class TestAlreadyLoadedGuard:
    """concept_relationship has no unique key, so a second load must be refused, not deduplicated."""

    def _engine(self, first_row):
        engine = MagicMock()
        conn = engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.first.return_value = first_row
        return engine, conn

    def test_loaded_means_the_scaffolding_concept_exists(self):
        engine, conn = self._engine((1,))
        assert dicom_vocabulary_loaded(engine) is True
        params = conn.execute.call_args.args[1]
        assert params == {"cid": DICOM_VOCABULARY_CONCEPT_ID}
        assert DICOM_VOCABULARY_CONCEPT_ID == 2128000000

    def test_not_loaded_on_a_fresh_database(self):
        engine, _ = self._engine(None)
        assert dicom_vocabulary_loaded(engine) is False

    def test_skip_if_loaded_touches_nothing_when_present(self, monkeypatch, tmp_path, capsys):
        engine, _ = self._engine((1,))
        monkeypatch.setattr("omop_db_tools.load_dicom_vocab.create_engine", lambda *a, **k: engine)
        monkeypatch.setattr("omop_db_tools.load_dicom_vocab.get_settings", lambda: MagicMock())
        loaded = MagicMock()
        monkeypatch.setattr("omop_db_tools.load_dicom_vocab.load_vocabulary_metadata", loaded)

        main(["--vocab-dir", str(tmp_path / "absent"), "--skip-if-loaded"])

        loaded.assert_not_called()
        assert "already loaded" in capsys.readouterr().out

    def test_skip_if_loaded_and_force_are_exclusive(self, tmp_path):
        with pytest.raises(SystemExit):
            main(["--vocab-dir", str(tmp_path), "--skip-if-loaded", "--force"])


class TestLoadFromCsv:
    """The CSV loaders, with safe_insert captured so no database is needed."""

    @pytest.fixture
    def inserted(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "omop_db_tools.load_dicom_vocab.safe_insert", lambda table, df, engine: calls.append((table, df))
        )
        return calls

    def _staging_csv(self, tmp_path, start_date):
        pd.DataFrame(
            {
                "concept_id": [2128000010],
                "valid_start_date": [start_date],
                "valid_end_date": [20991231],
                "standard_concept": ["S"],
                "invalid_reason": ["D"],
            }
        ).to_csv(tmp_path / "omop_table_staging_v5.csv", index=False)

    def test_concepts_get_parsed_dates_and_null_flags(self, tmp_path, inserted):
        self._staging_csv(tmp_path, 19930101)

        load_concepts(MagicMock(), tmp_path)

        [(table, df)] = inserted
        assert table == "CONCEPT"
        assert df["valid_start_date"].tolist() == [pd.Timestamp("1993-01-01")]
        assert df["valid_end_date"].tolist() == [pd.Timestamp("2099-12-31")]
        assert df["standard_concept"].tolist() == [None]
        assert df["invalid_reason"].tolist() == [None]

    def test_malformed_concept_date_raises_before_any_insert(self, tmp_path, inserted):
        self._staging_csv(tmp_path, "not-a-date")

        with pytest.raises(ValueError, match="not-a-date"):
            load_concepts(MagicMock(), tmp_path)

        assert inserted == []

    def test_relationships_merge_three_files_without_index_columns(self, tmp_path, inserted):
        # to_csv's default index is the "Unnamed: 0" column the upstream bundle ships.
        pd.DataFrame({"concept_id_1": [1], "concept_id_2": [2]}).to_csv(
            tmp_path / "part3_to_part16_relationship_via_CID.csv"
        )
        pd.DataFrame({"concept_id_1": [3], "concept_id_2": [4]}).to_csv(
            tmp_path / "cs_values_maps_to_value.csv", index=False
        )
        pd.DataFrame({"concept_id_1": [5], "concept_id_2": [6]}).to_csv(tmp_path / "cs_values_maps_to.csv")

        load_relationships(MagicMock(), tmp_path)

        [(table, df)] = inserted
        assert table == "CONCEPT_RELATIONSHIP"
        assert df.columns.tolist() == ["concept_id_1", "concept_id_2", "valid_start_date", "valid_end_date"]
        assert df["concept_id_1"].tolist() == [1, 3, 5]
        assert set(df["valid_start_date"]) == {date(1993, 1, 1)}
        assert set(df["valid_end_date"]) == {date(2099, 12, 31)}


class TestMain:
    def _run(self, monkeypatch, tmp_path, loaded, *flags):
        engine = MagicMock()
        engine.connect.return_value.__enter__.return_value.execute.return_value.first.return_value = loaded
        monkeypatch.setattr("omop_db_tools.load_dicom_vocab.create_engine", lambda *a, **k: engine)
        monkeypatch.setattr("omop_db_tools.load_dicom_vocab.get_settings", lambda: MagicMock())
        steps = MagicMock()
        for name in ("ensure_vocab_dir", "load_vocabulary_metadata", "load_concepts", "load_relationships"):
            monkeypatch.setattr(f"omop_db_tools.load_dicom_vocab.{name}", getattr(steps, name))

        main(["--vocab-dir", str(tmp_path), *flags])

        return engine, steps

    @pytest.mark.parametrize(
        ("loaded", "flags"),
        [(None, ["--skip-if-loaded"]), ((1,), ["--force"]), ((1,), [])],
        ids=["fresh-database", "forced", "unguarded"],
    )
    def test_loads_in_order_from_the_vocab_dir(self, monkeypatch, tmp_path, capsys, loaded, flags):
        engine, steps = self._run(monkeypatch, tmp_path, loaded, *flags)

        assert [call[0] for call in steps.mock_calls] == [
            "ensure_vocab_dir",
            "load_vocabulary_metadata",
            "load_concepts",
            "load_relationships",
        ]
        steps.ensure_vocab_dir.assert_called_once_with(tmp_path)
        steps.load_concepts.assert_called_once_with(engine, tmp_path)
        steps.load_relationships.assert_called_once_with(engine, tmp_path)
        assert "load complete" in capsys.readouterr().out
