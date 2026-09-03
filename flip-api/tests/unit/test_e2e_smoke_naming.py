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
"""The e2e smoke names its project and model for the tutorial it actually runs, not a hardcoded 'Xrays'."""

from pathlib import Path

import pytest

from tests.e2e_smoke import default_model_name, default_project_description, default_project_name, describe_tutorial


@pytest.mark.parametrize(
    ("path", "tutorial_dir", "label", "backend"),
    [
        ("../fl-tutorials/flower/ehr_risk_prediction/app", "ehr_risk_prediction", "EHR risk prediction", "flower"),
        (
            "../fl-tutorials/nvflare/tabular_classification/ehr_risk_prediction/app_files",
            "ehr_risk_prediction",
            "EHR risk prediction",
            "nvflare",
        ),
        (
            "../fl-tutorials/nvflare/image_classification/xray_classification/app_files",
            "xray_classification",
            "Chest X-ray classification",
            "nvflare",
        ),
        (
            "../fl-tutorials/flower/3d_spleen_segmentation_evaluation/app",
            "3d_spleen_segmentation_evaluation",
            "3D spleen segmentation evaluation",
            "flower",
        ),
        # An unknown app: title-cased directory name, no backend.
        ("/srv/my_custom-app/app", "my_custom-app", "My custom app", None),
    ],
)
def test_describe_tutorial_reads_the_tutorial_and_backend_off_the_app_path(path, tutorial_dir, label, backend):
    assert describe_tutorial(Path(path)) == (tutorial_dir, label, backend)


def test_defaults_name_the_tutorial_not_xrays():
    assert default_project_name("EHR risk prediction").startswith("EHR risk prediction E2E smoke ")
    assert default_model_name("EHR risk prediction") == "EHR risk prediction E2E smoke model"


def test_default_description_states_the_clinical_task_not_the_harness():
    """The projects list is read by clinicians: the description says what the project is for."""
    ehr = default_project_description("ehr_risk_prediction", "EHR risk prediction")
    assert ehr.startswith("Predicting which patients will go on to develop type 2 diabetes")
    for technical in ("smoke", "e2e", "backend", "flower", "nvflare", "cohort", ".py"):
        assert technical not in ehr.lower()

    xray = default_project_description("xray_classification", "Chest X-ray classification")
    assert "pleural effusion" in xray
    assert "chest X-rays" in xray

    unknown = default_project_description("my_custom-app", "My custom app")
    assert unknown == "Training the My custom app application across the participating trusts' data."
