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

"""Unit tests for the classification MAP's deepcOS engine-report scaffold."""

import json

import deepc_report
import pytest


class TestLabelCodings:
    def test_effusion_maps_to_snomed_pleural_effusion(self):
        coding = deepc_report.LABEL_CODINGS["Effusion"]
        assert coding.scheme == "SCT"
        assert coding.code == "60046008"

    def test_edema_maps_to_snomed_pulmonary_edema(self):
        coding = deepc_report.LABEL_CODINGS["Edema"]
        assert coding.scheme == "SCT"
        assert coding.code == "19242006"


class TestBuildFindings:
    def test_reports_every_label_with_an_explicit_present_absent_state(self):
        # Report both labels, not just the positive one, so a downstream system can accept or
        # reject each finding individually.
        findings = deepc_report.build_findings({"Effusion": 0.9, "Edema": 0.1}, threshold=0.5)
        by_meaning = {finding["concept"]["meaning"]: finding for finding in findings}
        assert len(findings) == 2
        assert by_meaning["Pleural effusion"]["present"] is True
        assert by_meaning["Pleural effusion"]["confidence"] == pytest.approx(0.9)
        assert by_meaning["Pulmonary edema"]["present"] is False
        assert by_meaning["Pulmonary edema"]["confidence"] == pytest.approx(0.1)

    def test_probability_at_the_threshold_counts_as_present(self):
        findings = deepc_report.build_findings({"Effusion": 0.5}, threshold=0.5)
        assert findings[0]["present"] is True

    def test_finding_carries_the_snomed_coding_for_its_label(self):
        findings = deepc_report.build_findings({"Effusion": 0.7}, threshold=0.5)
        assert findings[0]["concept"]["scheme"] == "SCT"
        assert findings[0]["concept"]["code"] == "60046008"

    def test_unmapped_label_falls_back_to_a_local_concept_rather_than_crashing(self):
        # The vendor documents an escape hatch for locally-defined concepts, so a label without a
        # standard term must still be reported, not dropped or raised on.
        findings = deepc_report.build_findings({"Cardiomegaly": 0.8}, threshold=0.5)
        assert findings[0]["concept"]["scheme"] == "LOCAL"
        assert findings[0]["concept"]["code"] == "Cardiomegaly"
        assert findings[0]["present"] is True


class TestBuildEngineReport:
    def test_carries_the_three_mandatory_fields(self):
        report = deepc_report.build_engine_report(
            findings=deepc_report.build_findings({"Effusion": 0.9}, threshold=0.5)
        )
        assert set(("jobStatus", "codedMessages", "engineResult")).issubset(report)

    def test_all_absent_findings_is_still_a_successful_analysis(self):
        # Unlike an empty segmentation, a classifier that ran and found nothing above threshold has
        # a valid negative result — success, with each finding marked absent.
        report = deepc_report.build_engine_report(
            findings=deepc_report.build_findings({"Effusion": 0.1, "Edema": 0.2}, threshold=0.5)
        )
        assert report["jobStatus"]["status"] == "SUCCESS"

    def test_all_negative_field_is_null_for_a_research_model(self):
        report = deepc_report.build_engine_report(
            findings=deepc_report.build_findings({"Effusion": 0.9}, threshold=0.5)
        )
        assert report["engineResult"]["allNegative"] is None


class TestWriteEngineReport:
    def test_written_filename_is_discoverable_by_the_deepcreport_convention(self, tmp_path):
        report = deepc_report.build_engine_report(
            findings=deepc_report.build_findings({"Effusion": 0.9}, threshold=0.5)
        )
        written = deepc_report.write_engine_report(tmp_path, report)
        assert "deepcreport" in written.name.lower()
        assert json.loads(written.read_text()) == report
