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
"""Unit tests for canonical dataset building, fetching, and per-trust splitting."""

import io
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import pytest

from omop_db_tools.dataset import SOURCE_TRUST_COLUMN, build_canonical, fetch_canonical, split_for_trust


def _canonical_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "person_id": [1, 2, 3, 4, 5, 6],
            "year_of_birth": [1980, 1981, 1982, 1983, 1984, 1985],
            SOURCE_TRUST_COLUMN: [1, 1, 1, 2, 2, 2],
        }
    )


class TestSplitForTrust:
    def test_legacy_reproduces_source_membership(self):
        df = _canonical_frame()
        trust_1 = split_for_trust(df, num_trusts=2, trust_index=1, mode="legacy")
        trust_2 = split_for_trust(df, num_trusts=2, trust_index=2, mode="legacy")

        assert sorted(trust_1["person_id"]) == [1, 2, 3]
        assert sorted(trust_2["person_id"]) == [4, 5, 6]

    def test_provenance_column_dropped(self):
        df = _canonical_frame()
        for mode in ["legacy", "modulo"]:
            part = split_for_trust(df, num_trusts=2, trust_index=1, mode=mode)
            assert SOURCE_TRUST_COLUMN not in part.columns

    def test_modulo_partitions_every_row_exactly_once(self):
        df = _canonical_frame()
        num_trusts = 3
        slices = [split_for_trust(df, num_trusts, index, mode="modulo") for index in range(1, num_trusts + 1)]

        all_person_ids = sorted(pid for part in slices for pid in part["person_id"])
        assert all_person_ids == sorted(df["person_id"])

    def test_modulo_is_deterministic(self):
        df = _canonical_frame()
        first = split_for_trust(df, num_trusts=2, trust_index=1, mode="modulo")
        second = split_for_trust(df, num_trusts=2, trust_index=1, mode="modulo")
        pd.testing.assert_frame_equal(first, second)

    def test_dependent_table_follows_person_partition(self):
        persons = _canonical_frame()
        dependents = pd.DataFrame(
            {
                "visit_occurrence_id": range(12),
                "person_id": [1, 2, 3, 4, 5, 6] * 2,
                SOURCE_TRUST_COLUMN: [1, 1, 1, 2, 2, 2] * 2,
            }
        )
        for mode in ["legacy", "modulo"]:
            person_part = split_for_trust(persons, num_trusts=2, trust_index=1, mode=mode)
            dependent_part = split_for_trust(dependents, num_trusts=2, trust_index=1, mode=mode)
            assert set(dependent_part["person_id"]) == set(person_part["person_id"])

    def test_empty_frame_passes_through(self):
        df = pd.DataFrame(columns=["person_id", SOURCE_TRUST_COLUMN])
        part = split_for_trust(df, num_trusts=2, trust_index=1, mode="legacy")
        assert part.empty
        assert SOURCE_TRUST_COLUMN not in part.columns

    @pytest.mark.parametrize(
        ("num_trusts", "trust_index"),
        [(0, 1), (2, 0), (2, 3), (-1, 1)],
    )
    def test_invalid_indices_rejected(self, num_trusts, trust_index):
        with pytest.raises(ValueError, match="must be"):
            split_for_trust(_canonical_frame(), num_trusts, trust_index, mode="modulo")

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError, match="Unknown partition mode"):
            split_for_trust(_canonical_frame(), 2, 1, mode="random")

    def test_legacy_without_provenance_column_rejected(self):
        df = _canonical_frame().drop(columns=[SOURCE_TRUST_COLUMN])
        with pytest.raises(ValueError, match=SOURCE_TRUST_COLUMN):
            split_for_trust(df, 2, 1, mode="legacy")

    def test_legacy_with_mismatched_trust_count_rejected(self):
        with pytest.raises(ValueError, match="use mode='modulo'"):
            split_for_trust(_canonical_frame(), num_trusts=3, trust_index=1, mode="legacy")

    def test_legacy_with_gapped_source_values_rejected(self):
        df = _canonical_frame()
        df[SOURCE_TRUST_COLUMN] = [1, 1, 1, 3, 3, 3]
        with pytest.raises(ValueError, match=r"exactly 1\.\.2"):
            split_for_trust(df, num_trusts=2, trust_index=1, mode="legacy")

    def test_nan_person_id_rejected(self):
        df = _canonical_frame()
        df.loc[0, "person_id"] = None
        for mode in ["legacy", "modulo"]:
            with pytest.raises(ValueError, match="missing values"):
                split_for_trust(df, 2, 1, mode=mode)

    def test_modulo_without_person_id_rejected(self):
        df = pd.DataFrame({"concept_id": [1, 2]})
        with pytest.raises(ValueError, match="person_id"):
            split_for_trust(df, 2, 1, mode="modulo")


class TestBuildCanonical:
    def _write_trust_dirs(self, root: Path) -> list[Path]:
        trust_dirs = []
        for trust_number, person_ids in [(1, [1, 2]), (2, [3, 4])]:
            trust_dir = root / f"trust_{trust_number}"
            project_dir = trust_dir / "cxr_project"
            project_dir.mkdir(parents=True)
            pd.DataFrame({"person_id": person_ids, "year_of_birth": [1980, 1990]}).to_csv(
                project_dir / "person.csv", index=False
            )
            pd.DataFrame({"visit_occurrence_id": person_ids, "person_id": person_ids}).to_csv(
                project_dir / "visit_occurrence.csv", index=False
            )
            pd.DataFrame(
                {
                    "image_occurrence_id": person_ids,
                    "person_id": person_ids,
                    "procedure_occurrence_id": person_ids,
                    "visit_occurrence_id": person_ids,
                }
            ).to_csv(project_dir / "image_occurrence.csv", index=False)
            pd.DataFrame({"image_feature_id": person_ids, "person_id": person_ids}).to_csv(
                project_dir / "image_feature.csv", index=False
            )
            pd.DataFrame({"procedure_occurrence_id": person_ids, "person_id": person_ids}).to_csv(
                project_dir / "procedure_occurrence.csv", index=False
            )
            trust_dirs.append(trust_dir)
        return trust_dirs

    def test_build_adds_provenance_and_roundtrips_via_legacy_split(self, tmp_path):
        trust_dirs = self._write_trust_dirs(tmp_path)
        dest = tmp_path / "canonical"

        build_canonical(trust_dirs, dest, projects=["cxr_project"])

        merged = pd.read_csv(dest / "cxr_project" / "person.csv")
        assert sorted(merged[SOURCE_TRUST_COLUMN].unique()) == [1, 2]
        assert len(merged) == 4

        trust_1 = split_for_trust(merged, num_trusts=2, trust_index=1, mode="legacy")
        original_trust_1 = pd.read_csv(trust_dirs[0] / "cxr_project" / "person.csv")
        pd.testing.assert_frame_equal(trust_1, original_trust_1)

    def test_optional_tables_may_be_absent(self, tmp_path):
        trust_dirs = self._write_trust_dirs(tmp_path)
        dest = tmp_path / "canonical"

        build_canonical(trust_dirs, dest, projects=["cxr_project"])

        assert not (dest / "cxr_project" / "measurement.csv").exists()
        assert not (dest / "cxr_project" / "observation.csv").exists()

    def test_missing_required_table_rejected(self, tmp_path):
        trust_dirs = self._write_trust_dirs(tmp_path)
        (trust_dirs[0] / "cxr_project" / "person.csv").unlink()
        (trust_dirs[1] / "cxr_project" / "person.csv").unlink()

        with pytest.raises(FileNotFoundError, match="person"):
            build_canonical(trust_dirs, tmp_path / "canonical", projects=["cxr_project"])

    def test_nonexistent_trust_dir_rejected(self, tmp_path):
        trust_dirs = self._write_trust_dirs(tmp_path)
        with pytest.raises(FileNotFoundError, match="does not exist"):
            build_canonical([trust_dirs[0], tmp_path / "trsut_2"], tmp_path / "canonical")

    def test_table_missing_from_one_source_rejected(self, tmp_path):
        trust_dirs = self._write_trust_dirs(tmp_path)
        (trust_dirs[1] / "cxr_project" / "person.csv").unlink()

        with pytest.raises(FileNotFoundError, match="silently dropped"):
            build_canonical(trust_dirs, tmp_path / "canonical", projects=["cxr_project"])


@contextmanager
def _fake_response(body: bytes):
    yield io.BytesIO(body)


def _fake_urlopen(responses: dict[str, bytes]):
    """Build an urlopen stub serving canned bodies; unknown URLs get a 404."""

    def opener(url, timeout=0):
        for fragment, body in responses.items():
            if fragment in url:
                return _fake_response(body)
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    return opener


class TestFetchCanonical:
    CSV = b"person_id,source_trust\n1,1\n"

    def test_optional_404_skipped_required_fetched(self, tmp_path, monkeypatch):
        required = ["person", "procedure_occurrence", "visit_occurrence", "image_occurrence", "image_feature"]
        served = {f"/{table}.csv": self.CSV for table in required}
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(served))

        fetch_canonical("v1", tmp_path, projects=["cxr_project"])

        assert (tmp_path / "cxr_project" / "person.csv").read_bytes() == self.CSV
        assert not (tmp_path / "cxr_project" / "measurement.csv").exists()
        assert not (tmp_path / "cxr_project" / "observation.csv").exists()

    def test_required_404_raises_with_version_hint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen({}))

        with pytest.raises(RuntimeError, match="version 'v9'"):
            fetch_canonical("v9", tmp_path, projects=["cxr_project"])

    def test_non_404_error_on_optional_table_raises(self, tmp_path, monkeypatch):
        def opener(url, timeout=0):
            if "/measurement.csv" in url:
                raise urllib.error.HTTPError(url, 500, "Server Error", None, None)
            return _fake_response(self.CSV)

        monkeypatch.setattr(urllib.request, "urlopen", opener)

        with pytest.raises(RuntimeError, match="HTTP 500"):
            fetch_canonical("v1", tmp_path, projects=["cxr_project"])

    def test_html_response_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda url, timeout=0: _fake_response(b"<!doctype html><html></html>")
        )

        with pytest.raises(RuntimeError, match="HTML, not CSV"):
            fetch_canonical("v1", tmp_path, projects=["cxr_project"])
