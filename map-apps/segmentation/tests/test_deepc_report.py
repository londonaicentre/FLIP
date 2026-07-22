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

"""Unit tests for the segmentation MAP's deepcOS engine-report scaffold."""

import json

import deepc_report
import numpy as np
import pytest


class TestSegmentedVolumeMl:
    def test_scales_foreground_voxel_count_by_voxel_volume(self):
        # 1000 foreground voxels at 1.5 x 1.5 x 2.0 mm = 4.5 mm^3 each = 4500 mm^3 = 4.5 mL.
        mask = np.zeros((10, 10, 20), dtype=np.uint8)
        mask.flat[:1000] = 1
        assert deepc_report.segmented_volume_ml(mask, (1.5, 1.5, 2.0)) == pytest.approx(4.5)

    def test_counts_every_nonzero_label_as_foreground(self):
        # A multi-label mask segments foreground wherever the label is non-zero.
        mask = np.array([[0, 1], [2, 0]], dtype=np.uint8)
        assert deepc_report.segmented_volume_ml(mask, (1.0, 1.0)) == pytest.approx(0.002)

    def test_empty_mask_is_zero_millilitres(self):
        mask = np.zeros((4, 4, 4), dtype=np.uint8)
        assert deepc_report.segmented_volume_ml(mask, (1.0, 1.0, 1.0)) == 0.0

    def test_spacing_length_must_match_mask_dimensions(self):
        mask = np.ones((4, 4, 4), dtype=np.uint8)
        with pytest.raises(ValueError, match="spacing"):
            deepc_report.segmented_volume_ml(mask, (1.0, 1.0))


class TestVoxelSpacing:
    def test_reads_the_three_spacings_from_monai_deploy_metadata(self):
        metadata = {"row_pixel_spacing": 0.7, "col_pixel_spacing": 0.7, "depth_pixel_spacing": 1.5}
        spacing = deepc_report.voxel_spacing(metadata)
        # Order is irrelevant to a volume — only the product is used — but all three must be present.
        assert sorted(spacing) == pytest.approx([0.7, 0.7, 1.5])

    def test_missing_spacing_raises_rather_than_assuming_a_default(self):
        # An assumed 1 mm spacing would silently corrupt the reported volume, so a missing spacing
        # is a hard error the operator turns into a degraded report, not a fabricated number.
        with pytest.raises(ValueError, match="spacing"):
            deepc_report.voxel_spacing({"row_pixel_spacing": 0.7, "col_pixel_spacing": 0.7})


class TestBuildEngineReport:
    def _quantity(self, volume_ml=4.5):
        return deepc_report.volume_quantity(volume_ml)

    def test_carries_the_three_mandatory_fields(self):
        report = deepc_report.build_engine_report(quantities=[self._quantity()])
        assert set(("jobStatus", "codedMessages", "engineResult")).issubset(report)

    def test_success_status_when_a_positive_volume_was_measured(self):
        report = deepc_report.build_engine_report(quantities=[self._quantity(4.5)])
        assert report["jobStatus"]["status"] == "SUCCESS"

    def test_empty_segmentation_is_flagged_not_reported_as_plain_success(self):
        # A process that segments nothing must not claim success silently: the volume is zero,
        # so the status is degraded and a coded message explains why.
        report = deepc_report.build_engine_report(quantities=[self._quantity(0.0)])
        assert report["jobStatus"]["status"] != "SUCCESS"
        assert any("empty" in message["message"].lower() for message in report["codedMessages"])

    def test_all_negative_field_is_null_for_a_research_model(self):
        # Reserved by the vendor for regulatory-cleared engines; a FLIP research model leaves it null.
        report = deepc_report.build_engine_report(quantities=[self._quantity()])
        assert report["engineResult"]["allNegative"] is None

    def test_volume_quantity_codes_millilitres_and_an_anatomical_attribute(self):
        quantity = deepc_report.volume_quantity(4.5, anatomy=deepc_report.SPLEEN)
        assert quantity["value"] == pytest.approx(4.5)
        assert quantity["unit"]["code"] == "mL"
        assert quantity["anatomy"]["scheme"] == "SCT"
        assert quantity["anatomy"]["code"] == deepc_report.SPLEEN.code


class TestWriteEngineReport:
    def test_written_filename_is_discoverable_by_the_deepcreport_convention(self, tmp_path):
        report = deepc_report.build_engine_report(quantities=[deepc_report.volume_quantity(4.5)])
        written = deepc_report.write_engine_report(tmp_path, report)
        assert "deepcreport" in written.name.lower()
        assert json.loads(written.read_text()) == report
