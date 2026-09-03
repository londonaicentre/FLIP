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
"""The e2e smoke names its project and model for the tutorial it actually runs, not a hardcoded 'Xrays',
and reads ``has_imaging`` back off the hub rather than trusting its own flag."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.e2e_smoke import (
    SmokeFailure,
    TutorialInfo,
    create_project_with_query,
    default_model_name,
    default_project_name,
    describe_tutorial,
    project_has_imaging,
)


@pytest.mark.parametrize(
    ("path", "tutorial_dir", "label"),
    [
        ("../fl-tutorials/flower/ehr_risk_prediction/app", "ehr_risk_prediction", "EHR risk prediction"),
        (
            "../fl-tutorials/nvflare/tabular_classification/ehr_risk_prediction/app_files",
            "ehr_risk_prediction",
            "EHR risk prediction",
        ),
        (
            "../fl-tutorials/nvflare/image_classification/xray_classification/app_files",
            "xray_classification",
            "Chest X-ray classification",
        ),
        (
            "../fl-tutorials/flower/3d_spleen_segmentation_evaluation/app",
            "3d_spleen_segmentation_evaluation",
            "3D spleen segmentation evaluation",
        ),
        # An unknown app: capitalised, de-hyphenated directory name.
        ("/srv/my_custom-app/app", "my_custom-app", "My custom app"),
    ],
)
def test_describe_tutorial_reads_the_tutorial_off_the_app_path(path, tutorial_dir, label):
    info = describe_tutorial(Path(path))
    assert isinstance(info, TutorialInfo)
    assert (info.dir, info.label) == (tutorial_dir, label)


def test_defaults_name_the_tutorial_not_xrays():
    assert default_project_name("EHR risk prediction").startswith("EHR risk prediction E2E smoke ")
    assert default_model_name("EHR risk prediction") == "EHR risk prediction E2E smoke model"


def test_task_states_the_clinical_task_not_the_harness():
    """The projects list is read by clinicians: the description says what the project is for."""
    ehr = describe_tutorial(Path("../fl-tutorials/flower/ehr_risk_prediction/app")).task
    assert ehr.startswith("Predicting which patients will go on to develop type 2 diabetes")
    for technical in ("smoke", "e2e", "backend", "flower", "nvflare", "cohort", ".py"):
        assert technical not in ehr.lower()

    xray = describe_tutorial(Path("../fl-tutorials/nvflare/image_classification/xray_classification/app_files")).task
    assert "pleural effusion" in xray
    assert "chest X-rays" in xray

    unknown = describe_tutorial(Path("/srv/my_custom-app/app")).task
    assert unknown == "Training the My custom app application across the participating trusts' data."


def _response(status_code: int, body: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    response.text = str(body)
    return response


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({}, True),  # a hub predating the flag omits it: imaging, the old behaviour
        ({"has_imaging": True}, True),
        ({"has_imaging": False}, False),
    ],
)
def test_project_has_imaging_reads_the_flag_off_the_project(body, expected):
    with patch("tests.e2e_smoke._get", return_value=_response(200, body)):
        assert project_has_imaging(MagicMock(), {}, "proj-1") is expected


def test_project_has_imaging_fails_loudly_on_an_error_response():
    with patch("tests.e2e_smoke._get", return_value=_response(404, {"detail": "not found"})):
        with pytest.raises(SmokeFailure):
            project_has_imaging(MagicMock(), {}, "proj-1")


def test_create_project_fails_fast_when_the_hub_ignores_no_imaging():
    """A hub predating FLIP#1071 drops the unknown field: abort now, not on a 20-minute pull wait."""
    posts = [_response(201, {"id": "proj-1"}), _response(200, {"query_id": "q-1"})]
    with (
        patch("tests.e2e_smoke._post", side_effect=posts) as mock_post,
        patch("tests.e2e_smoke._get", return_value=_response(200, {"id": "proj-1"})),  # no has_imaging key
    ):
        with pytest.raises(SmokeFailure, match="ignored has_imaging=false"):
            create_project_with_query(MagicMock(), {}, "EHR E2E smoke", "SELECT 1", has_imaging=False, description="d")

    posted = mock_post.call_args_list[0].args[2]
    assert posted["has_imaging"] is False
    assert posted["description"] == "d"


def test_create_project_accepts_a_hub_that_honours_no_imaging():
    # create project → save cohort query → submit it to the trusts
    posts = [_response(201, {"id": "proj-1"}), _response(200, {"query_id": "q-1"}), _response(202, {})]
    with (
        patch("tests.e2e_smoke._post", side_effect=posts),
        patch("tests.e2e_smoke._get", return_value=_response(200, {"id": "proj-1", "has_imaging": False})),
    ):
        assert create_project_with_query(MagicMock(), {}, "n", "SELECT 1", has_imaging=False) == ("proj-1", "q-1")
