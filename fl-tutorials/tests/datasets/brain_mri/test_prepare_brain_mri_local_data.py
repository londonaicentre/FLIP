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

"""The simulator (LOCAL_DEV) layout: per-modality 3-D NIfTIs under the same accessions the DICOM path uses."""

from __future__ import annotations

import csv

import nibabel as nib
import numpy as np
from brain_mri import prepare_brain_mri_local_data as prep
from utils import synthetic_identity


class TestPrepare:
    def test_layout_matches_what_the_platform_delivers(self, msd_extract, tmp_path):
        out = tmp_path / "brain_mri"
        rows = prep.prepare(
            msd_extract / "imagesTr",
            msd_extract / "labelsTr",
            msd_extract / "dataset.json",
            out,
            ["BRATS_001", "BRATS_002"],
        )

        accession = synthetic_identity.accession_number("BRATS_001")
        scans = out / "images" / accession / "scans"
        assert sorted(p.name for p in scans.iterdir()) == [
            "input_FLAIR_BRATS_001.nii.gz",
            "input_T1Gd_BRATS_001.nii.gz",
            "input_T1w_BRATS_001.nii.gz",
            "input_T2w_BRATS_001.nii.gz",
            "label_BRATS_001.nii.gz",
        ]
        assert rows == [
            {"accession_id": accession, "subject": "BRATS_001"},
            {"accession_id": synthetic_identity.accession_number("BRATS_002"), "subject": "BRATS_002"},
        ]
        with (out / "dataframe.csv").open() as handle:
            assert list(csv.DictReader(handle)) == rows

    def test_each_channel_is_split_out_with_the_source_affine(self, msd_extract, tmp_path):
        out = tmp_path / "brain_mri"
        prep.prepare(
            msd_extract / "imagesTr", msd_extract / "labelsTr", msd_extract / "dataset.json", out, ["BRATS_003"]
        )
        source = nib.load(str(msd_extract / "imagesTr" / "BRATS_003.nii.gz"))
        scans = out / "images" / synthetic_identity.accession_number("BRATS_003") / "scans"

        t1gd = nib.load(str(scans / "input_T1Gd_BRATS_003.nii.gz"))
        assert t1gd.shape == source.shape[:3]
        np.testing.assert_array_equal(t1gd.get_fdata(), source.get_fdata()[..., 2])
        np.testing.assert_array_equal(t1gd.affine, source.affine)
        label = nib.load(str(scans / "label_BRATS_003.nii.gz"))
        assert label.shape == source.shape[:3]

    def test_main_selects_the_lowest_n(self, msd_extract, tmp_path):
        out = tmp_path / "brain_mri"
        argv = ["--images-dir", str(msd_extract / "imagesTr"), "--labels-dir", str(msd_extract / "labelsTr")]
        argv += ["--dataset-json", str(msd_extract / "dataset.json"), "--out-dir", str(out), "--num-cases", "1"]
        prep.main(argv)
        assert [p.name for p in (out / "images").iterdir()] == [synthetic_identity.accession_number("BRATS_001")]
