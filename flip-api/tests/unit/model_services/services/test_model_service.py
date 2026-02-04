# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from flip_api.db.models.main_models import FLMetrics, Model
from flip_api.domain.interfaces.model import IModelDetails
from flip_api.domain.schemas.status import ModelStatus
from flip_api.model_services.services.model_service import (
    add_log,
    delete_model,
    delete_models,
    edit_model,
    get_metrics,
    get_model_status,
    update_model_status,
    validate_trusts,
)


def test_edit_model_success():
    session = MagicMock()
    model_id = uuid4()
    user_id = "user"
    model_details = IModelDetails(name="NewName", description="Updated")

    mock_model = MagicMock()
    session.get.return_value = mock_model

    edit_model(model_id, model_details, user_id, session)

    session.get.assert_called_once_with(Model, model_id)
    assert mock_model.name == model_details.name
    assert mock_model.description == model_details.description
    session.commit.assert_called()


def test_edit_model_not_found():
    session = MagicMock()
    session.get.return_value = None
    with pytest.raises(ValueError, match="not found"):
        edit_model(uuid4(), MagicMock(), "user", session)


def test_update_model_status_success():
    session = MagicMock()
    model_id = uuid4()
    mock_model = MagicMock()
    session.get.return_value = mock_model

    result = update_model_status(model_id, ModelStatus.INITIATED, session)

    assert result == ModelStatus.INITIATED
    session.commit.assert_called()
    assert mock_model.status == ModelStatus.INITIATED


def test_update_model_status_model_not_found():
    session = MagicMock()
    session.get.return_value = None
    result = update_model_status(uuid4(), ModelStatus.STOPPED, session)
    assert result is None


def test_add_log_success():
    session = MagicMock()
    add_log(uuid4(), "Log message", session)
    session.add.assert_called()
    session.commit.assert_called()


def test_add_log_failure():
    session = MagicMock()
    session.commit.side_effect = Exception("DB error")
    with pytest.raises(Exception, match="DB error"):
        add_log(uuid4(), "Log message", session)
    session.rollback.assert_called()


def test_delete_model_success():
    session = MagicMock()
    mock_model = MagicMock()
    session.get.return_value = mock_model

    delete_model(uuid4(), "user", session)
    session.commit.assert_called()
    assert mock_model.deleted is True


def test_delete_model_not_found():
    session = MagicMock()
    session.get.return_value = None
    with pytest.raises(ValueError, match="not found"):
        delete_model(uuid4(), "user", session)


def test_delete_models_success():
    session = MagicMock()
    project_id = uuid4()
    user_id = "user"

    model1 = MagicMock(id=uuid4(), deleted=False)
    model2 = MagicMock(id=uuid4(), deleted=False)
    session.exec.return_value.all.return_value = [model1, model2]

    result = delete_models(project_id, user_id, session)

    session.commit.assert_called()
    assert model1.deleted is True
    assert model2.deleted is True
    assert result == 2


def test_delete_models_none_found():
    session = MagicMock()
    session.exec.return_value.all.return_value = []
    with pytest.raises(ValueError, match="Failed to delete models"):
        delete_models(uuid4(), "user", session)


def test_get_model_status_found():
    session = MagicMock()
    mock_model = MagicMock(status=ModelStatus.INITIATED, deleted=False)
    session.get.return_value = mock_model

    result = get_model_status(uuid4(), session)
    assert result.status == ModelStatus.INITIATED
    assert result.deleted is False


def test_get_model_status_not_found():
    session = MagicMock()
    session.get.return_value = None
    result = get_model_status(uuid4(), session)
    assert result is None


def test_validate_trusts_all_valid():
    session = MagicMock()
    model_id = uuid4()
    trusts = ["Trust A", "Trust B"]
    session.exec.return_value.all.return_value = ["Trust A", "Trust B", "Trust C"]

    result = validate_trusts(model_id, trusts, session)
    assert result is True


def test_validate_trusts_some_invalid():
    session = MagicMock()
    model_id = uuid4()
    trusts = ["Trust A", "Trust B"]
    # Simulate that Trust B is not in the database
    session.exec.return_value.all.return_value = ["Trust A", "Trust C"]

    result = validate_trusts(model_id, trusts, session)
    assert result is False


def test_get_metrics():
    session = MagicMock()
    model_id = uuid4()

    mock_metric1 = FLMetrics(model_id=model_id, label="accuracy", trust="trust1", global_round=1, result=0.9)
    mock_metric2 = FLMetrics(model_id=model_id, label="accuracy", trust="trust1", global_round=2, result=0.92)
    mock_metric3 = FLMetrics(model_id=model_id, label="accuracy", trust="trust2", global_round=1, result=0.88)

    session.exec.return_value.all.return_value = [mock_metric1, mock_metric2, mock_metric3]

    result = get_metrics(model_id, session)

    assert len(result) == 1
    assert result[0].yLabel == "accuracy"
    assert result[0].xLabel == "globalRound"
    assert len(result[0].metrics) == 2  # trust1 and trust2

    trust_labels = sorted([m.seriesLabel for m in result[0].metrics])
    assert trust_labels == ["trust1", "trust2"]

    trust1_data = next(m for m in result[0].metrics if m.seriesLabel == "trust1").data
    assert trust1_data[0].xValue == 1
    assert trust1_data[0].yValue == 0.9
    assert trust1_data[1].xValue == 2
    assert trust1_data[1].yValue == 0.92


def test_get_metrics_no_results():
    session = MagicMock()
    model_id = uuid4()

    session.exec.return_value.all.return_value = []

    result = get_metrics(model_id, session)

    assert len(result) == 0
