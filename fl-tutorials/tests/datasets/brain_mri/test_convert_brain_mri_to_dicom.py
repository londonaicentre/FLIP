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

"""MSD 4-D volume → one MR study of four series, flat per accession, byte-identical on every run."""

from __future__ import annotations

import hashlib
from pathlib import Path

import nibabel as nib
import numpy as np
import pydicom
import pytest
from brain_mri import convert_brain_mri_to_dicom as conv
from conftest import MODALITIES, SHAPE
from utils import synthetic_identity

CHANNEL_LABELS = ["FLAIR", "T1w", "T1Gd", "T2w"]


def _series(accession_dir: Path) -> dict[int, list[pydicom.Dataset]]:
    """Instances grouped by SeriesNumber, in InstanceNumber order."""
    grouped: dict[int, list[pydicom.Dataset]] = {}
    for path in accession_dir.glob("*.dcm"):
        ds = pydicom.dcmread(str(path))
        grouped.setdefault(int(ds.SeriesNumber), []).append(ds)
    return {n: sorted(v, key=lambda d: int(d.InstanceNumber)) for n, v in grouped.items()}


class TestReadModalities:
    def test_channel_order_comes_from_dataset_json(self, msd_extract):
        assert conv.read_modalities(msd_extract / "dataset.json") == ["FLAIR", "T1w", "t1gd", "T2w"]

    def test_every_msd_channel_has_a_series_spec(self):
        assert set(conv.CHANNELS) == set(MODALITIES.values())
        assert [conv.CHANNELS[m].label for m in MODALITIES.values()] == CHANNEL_LABELS


class TestConvertCase:
    def test_one_study_four_series_flat_under_the_accession(self, msd_extract, tmp_path):
        out = tmp_path / "dicom"
        accession_dir = conv.convert_case(
            "BRATS_001", msd_extract / "imagesTr", conv.read_modalities(msd_extract / "dataset.json"), out
        )

        expected = out / synthetic_identity.accession_number("BRATS_001")
        assert accession_dir == expected
        assert not any(p.is_dir() for p in accession_dir.iterdir()), "flat: every instance directly under the accession"
        series = _series(accession_dir)
        assert sorted(series) == [1, 2, 3, 4]
        assert all(len(instances) == SHAPE[2] for instances in series.values())
        assert len({ds.StudyInstanceUID for group in series.values() for ds in group}) == 1
        assert len({ds.FrameOfReferenceUID for group in series.values() for ds in group}) == 1
        assert len({ds.SeriesInstanceUID for group in series.values() for ds in group}) == 4
        for number, label in enumerate(CHANNEL_LABELS, start=1):
            first = series[number][0]
            assert (first.SeriesDescription, first.ProtocolName) == (label, label)
            assert first.Modality == "MR"
            assert first.BodyPartExamined == "BRAIN"
            assert first.StudyDescription == conv.STUDY_DESCRIPTION
            assert first.ClinicalTrialSubjectID == "BRATS_001"
            assert first.AccessionNumber == expected.name

    def test_each_series_carries_its_own_channel(self, msd_extract, tmp_path):
        out = tmp_path / "dicom"
        modalities = conv.read_modalities(msd_extract / "dataset.json")
        accession_dir = conv.convert_case("BRATS_002", msd_extract / "imagesTr", modalities, out)
        volume = nib.load(str(msd_extract / "imagesTr" / "BRATS_002.nii.gz")).get_fdata()

        for number, instances in _series(accession_dir).items():
            channel = number - 1
            for k, ds in enumerate(instances):
                expected = np.clip(np.rint(volume[:, :, k, channel].T), 0, 65535).astype(np.uint16)
                np.testing.assert_array_equal(ds.pixel_array, expected)

    def test_series_acquisition_tags_differ_by_channel(self, msd_extract, tmp_path):
        accession_dir = conv.convert_case(
            "BRATS_001", msd_extract / "imagesTr", conv.read_modalities(msd_extract / "dataset.json"), tmp_path
        )
        series = _series(accession_dir)
        flair, t1gd, t2w = series[1][0], series[3][0], series[4][0]
        assert "IR" in list(flair.ScanningSequence)
        assert t1gd.ContrastBolusAgent
        assert "ContrastBolusAgent" not in flair
        assert float(t2w.EchoTime) > float(series[2][0].EchoTime), "T2w has the long echo"
        assert float(flair.MagneticFieldStrength) in {1.5, 3.0}
        assert flair.ScanOptions is not None

    def test_a_channel_subset_writes_only_those_series(self, msd_extract, tmp_path):
        modalities = conv.read_modalities(msd_extract / "dataset.json")
        accession_dir = conv.convert_case(
            "BRATS_001", msd_extract / "imagesTr", modalities, tmp_path, channels=["FLAIR", "T2w"]
        )
        series = _series(accession_dir)
        assert sorted(series) == [1, 4], "series numbers stay tied to the channel index"

    def test_reconversion_is_byte_identical(self, msd_extract, tmp_path):
        modalities = conv.read_modalities(msd_extract / "dataset.json")
        a = conv.convert_case("BRATS_001", msd_extract / "imagesTr", modalities, tmp_path / "a")
        b = conv.convert_case("BRATS_001", msd_extract / "imagesTr", modalities, tmp_path / "b")
        digest = lambda d: {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in d.glob("*.dcm")}  # noqa: E731
        assert digest(a) == digest(b)

    def test_a_volume_with_the_wrong_channel_count_is_refused(self, msd_extract, tmp_path):
        with pytest.raises(SystemExit, match="channel"):
            conv.convert_case("BRATS_001", msd_extract / "imagesTr", ["FLAIR", "T1w"], tmp_path)


class TestConvert:
    def test_converts_the_selected_cases_and_reports_accessions(self, msd_extract, tmp_path):
        out = tmp_path / "dicom"
        written = conv.convert(msd_extract / "imagesTr", msd_extract / "dataset.json", out, ["BRATS_001", "BRATS_003"])

        assert set(written) == {"BRATS_001", "BRATS_003"}
        assert all(path.parent == out for path in written.values())
        assert len(list(out.iterdir())) == 2

    def test_parallel_workers_produce_the_same_tree(self, msd_extract, tmp_path):
        cases = ["BRATS_001", "BRATS_002", "BRATS_003"]
        serial = conv.convert(msd_extract / "imagesTr", msd_extract / "dataset.json", tmp_path / "s", cases, workers=1)
        parallel = conv.convert(
            msd_extract / "imagesTr", msd_extract / "dataset.json", tmp_path / "p", cases, workers=3
        )
        for case in cases:
            assert sorted(p.name for p in serial[case].glob("*.dcm")) == sorted(
                p.name for p in parallel[case].glob("*.dcm")
            )


class TestMain:
    def test_cli_num_cases_and_metadata_table_are_exclusive(self, msd_extract, tmp_path):
        argv = ["--images-dir", str(msd_extract / "imagesTr"), "--dataset-json", str(msd_extract / "dataset.json")]
        argv += ["--out-dir", str(tmp_path), "--num-cases", "1", "--metadata-table", str(tmp_path / "x.csv")]
        with pytest.raises(SystemExit):
            conv.main(argv)

    def test_cli_converts_the_lowest_n_cases(self, msd_extract, tmp_path):
        out = tmp_path / "dicom"
        conv.main(["--images-dir", str(msd_extract / "imagesTr"), "--dataset-json", str(msd_extract / "dataset.json"),
                   "--out-dir", str(out), "--num-cases", "2"])  # fmt: skip
        expected = {synthetic_identity.accession_number(c) for c in ("BRATS_001", "BRATS_002")}
        assert {p.name for p in out.iterdir()} == expected
