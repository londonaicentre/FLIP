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

from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from flip_api.db.models.main_models import FLMetrics, Model
from flip_api.domain.interfaces.model import IModelDetails
from flip_api.domain.schemas.status import ModelStatus
from flip_api.domain.schemas.types import FLLogEvent
from flip_api.model_services.services.model_service import (
    _run_trusts_by_model,
    add_log,
    delete_model,
    delete_models,
    edit_model,
    get_metrics,
    get_model_status,
    queued_positions_by_model,
    resolve_trust_from_fl_client_name,
    update_model_status,
    validate_trust_ids,
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


def test_update_model_status_results_upload_failed_triggers_scheduler():
    session = MagicMock()
    model_id = uuid4()
    session.get.return_value = MagicMock()

    with patch("flip_api.model_services.services.model_service.fl_scheduler_service") as mock_scheduler:
        result = update_model_status(model_id, ModelStatus.RESULTS_UPLOAD_FAILED, session)

    assert result == ModelStatus.RESULTS_UPLOAD_FAILED
    mock_scheduler.update_fl_scheduler.assert_called_once_with(model_id, session)


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


def test_add_log_persists_event_fields():
    """Typed event rows land with event_type/global_round/details and no display text."""
    session = MagicMock()
    add_log(
        uuid4(),
        None,
        session,
        event_type=FLLogEvent.ROUND_STARTED,
        global_round=7,
        details={"total_rounds": 15},
    )
    row = session.add.call_args.args[0]
    assert row.log is None
    assert row.event_type == FLLogEvent.ROUND_STARTED
    assert row.global_round == 7
    assert row.details == {"total_rounds": 15}


def test_add_log_event_fields_default_null():
    """Legacy free-text writes stay exactly as they were: all event columns null."""
    session = MagicMock()
    add_log(uuid4(), "Log message", session)
    row = session.add.call_args.args[0]
    assert row.log == "Log message"
    assert row.event_type is None
    assert row.global_round is None
    assert row.details is None


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


def test_resolve_trust_resolves_kit_slot():
    mock_trust = MagicMock()
    session = MagicMock()
    session.exec.return_value.first.return_value = mock_trust

    result = resolve_trust_from_fl_client_name("Trust_2", session)
    assert result is mock_trust


def test_resolve_trust_unassigned_slot():
    session = MagicMock()
    # No slot row matches, or the slot is not assigned to a trust.
    session.exec.return_value.first.return_value = None

    result = resolve_trust_from_fl_client_name("Trust_9", session)
    assert result is None


def test_resolve_trust_resolves_by_kit_slot():
    """The FL kit slot (the client name) resolves via FLKitSlot.slot_name.

    Resolution is uniform across backends and independent of the operator-chosen
    trust display name (see #538): NVFLARE reports the certificate CN, Flower the
    SUPERNODE_NAME (set to FL_KIT_SLOT) — both are the slot, so no backend branch
    (and no get_settings()) is involved.
    """
    session = MagicMock()
    mock_trust = MagicMock()
    session.exec.return_value.first.return_value = mock_trust

    result = resolve_trust_from_fl_client_name("Trust_1", session)

    assert result is mock_trust
    stmt_sql = str(session.exec.call_args[0][0]).lower()
    assert "slot_name" in stmt_sql, f"expected slot-based resolution, got: {stmt_sql}"


def test_get_metrics():
    session = MagicMock()
    model_id = uuid4()
    trust_a = uuid4()
    trust_b = uuid4()

    m1 = FLMetrics(
        model_id=model_id, trust=trust_a, fl_client_name="Trust_1", label="accuracy",
        global_round=1, x_value=1.0, result=0.9,
    )
    m2 = FLMetrics(
        model_id=model_id, trust=trust_a, fl_client_name="Trust_1", label="accuracy",
        global_round=2, x_value=2.0, result=0.92,
    )
    m3 = FLMetrics(
        model_id=model_id, trust=trust_b, fl_client_name="Trust_2", label="accuracy",
        global_round=1, x_value=1.0, result=0.88,
    )

    # First exec = FLMetrics rows; second exec = trust id -> (code, name) lookup.
    session.exec.side_effect = [
        MagicMock(all=MagicMock(return_value=[m1, m2, m3])),
        MagicMock(all=MagicMock(return_value=[(trust_a, None, "Trust A"), (trust_b, None, "Trust B")])),
    ]

    result = get_metrics(model_id, session)

    assert len(result) == 1
    assert result[0].y_label == "accuracy"
    assert result[0].x_label == "Global Rounds"
    assert len(result[0].metrics) == 2  # trust_a and trust_b

    labels = sorted(m.series_label for m in result[0].metrics)
    assert labels == ["Trust A", "Trust B"]

    trust_a_data = next(m for m in result[0].metrics if m.series_label == "Trust A").data
    assert trust_a_data[0].x_value == 1
    assert trust_a_data[0].y_value == 0.9
    assert trust_a_data[1].x_value == 2
    assert trust_a_data[1].y_value == 0.92

    # The rows query is deliberately unordered: the stable in-memory sort owns the series order
    # (asserted above), and a DB ORDER BY x_value would return equal-x rows in unspecified order,
    # weakening the ties-keep-insertion-order guarantee.
    rows_stmt_sql = str(session.exec.call_args_list[0][0][0]).lower()
    assert "order by" not in rows_stmt_sql
    assert "x_value" in rows_stmt_sql


def test_get_metrics_plots_at_x_value_not_global_round():
    """xValue is the stored plot coordinate; global_round is provenance only (FLIP#148)."""
    session = MagicMock()
    model_id = uuid4()
    trust_a = uuid4()

    m = FLMetrics(
        model_id=model_id, trust=trust_a, fl_client_name="Trust_1", label="VAL_LOSS",
        global_round=1, x_value=7.5, x_label="epoch", result=0.42,
    )

    session.exec.side_effect = [
        MagicMock(all=MagicMock(return_value=[m])),
        MagicMock(all=MagicMock(return_value=[(trust_a, "GSTT", "Trust A")])),
    ]

    result = get_metrics(model_id, session)

    assert result[0].x_label == "epoch"
    assert result[0].metrics[0].data[0].x_value == 7.5


def test_get_metrics_separates_plots_by_x_label():
    """Same metric label under different x-axis labels -> separate plots, not one merged chart (FLIP#148)."""
    session = MagicMock()
    model_id = uuid4()
    trust_a = uuid4()

    # Identical yLabel ("loss"), two different x-axis labels -> must not collapse into one plot.
    m_epoch = FLMetrics(
        model_id=model_id, trust=trust_a, fl_client_name="Trust_1", label="loss",
        x_label="epoch", global_round=1, x_value=1.0, result=0.9,
    )
    m_round = FLMetrics(
        model_id=model_id, trust=trust_a, fl_client_name="Trust_1", label="loss",
        x_label="Global Rounds", global_round=1, x_value=1.0, result=0.8,
    )

    session.exec.side_effect = [
        MagicMock(all=MagicMock(return_value=[m_epoch, m_round])),
        MagicMock(all=MagicMock(return_value=[(trust_a, "GSTT", "Trust A")])),
    ]

    result = get_metrics(model_id, session)

    assert len(result) == 2
    assert {(m.y_label, m.x_label) for m in result} == {("loss", "epoch"), ("loss", "Global Rounds")}
    # Each plot holds only its own point.
    for plot in result:
        assert len(plot.metrics) == 1
        assert len(plot.metrics[0].data) == 1


def test_get_metrics_resolves_trust_to_code():
    """seriesLabel uses the trust's `code` when one is set."""
    session = MagicMock()
    model_id = uuid4()
    trust_1 = uuid4()
    trust_2 = uuid4()

    m1 = FLMetrics(
        model_id=model_id, trust=trust_1, fl_client_name="Trust_1", label="accuracy",
        global_round=1, x_value=1.0, result=0.9,
    )
    m2 = FLMetrics(
        model_id=model_id, trust=trust_2, fl_client_name="Trust_2", label="accuracy",
        global_round=1, x_value=1.0, result=0.85,
    )

    session.exec.side_effect = [
        MagicMock(all=MagicMock(return_value=[m1, m2])),
        MagicMock(all=MagicMock(return_value=[(trust_1, "GSTT", "Trust One"), (trust_2, "UCLH", "Trust Two")])),
    ]

    result = get_metrics(model_id, session)

    labels = sorted(m.series_label for m in result[0].metrics)
    assert labels == ["GSTT", "UCLH"]


def test_get_metrics_falls_back_to_trust_name_when_no_code():
    """A trust without a `code` uses the trust name as the legend label."""
    session = MagicMock()
    model_id = uuid4()
    trust_3 = uuid4()

    m = FLMetrics(
        model_id=model_id, trust=trust_3, fl_client_name="Trust_3", label="accuracy",
        global_round=1, x_value=1.0, result=0.8,
    )

    session.exec.side_effect = [
        MagicMock(all=MagicMock(return_value=[m])),
        MagicMock(all=MagicMock(return_value=[(trust_3, None, "Trust Three")])),
    ]

    result = get_metrics(model_id, session)

    assert result[0].metrics[0].series_label == "Trust Three"


def test_get_metrics_no_results():
    session = MagicMock()
    model_id = uuid4()

    session.exec.return_value.all.return_value = []

    result = get_metrics(model_id, session)

    assert len(result) == 0


# ---------------------------------------------------------------------------
# update_model_status — additional branches: passing status=None preserves the
# current status; audit + scheduler side-effects fire on terminal transitions.
# ---------------------------------------------------------------------------


@patch("flip_api.model_services.services.model_service.audit_model_action")
def test_update_model_status_with_none_keeps_existing_status(mock_audit):
    """status=None means "re-emit the current status" — used by callers that
    just want the value back without mutating it. The audit map only fires
    when the status actually changes, so no audit row is written here.
    """
    session = MagicMock()
    mock_model = MagicMock(status=ModelStatus.INITIATED)
    session.get.return_value = mock_model

    result = update_model_status(uuid4(), None, session)

    assert result == ModelStatus.INITIATED
    # No transition → no audit insert.
    mock_audit.assert_not_called()


@patch("flip_api.model_services.services.model_service.audit_model_action")
def test_update_model_status_writes_audit_on_transition_to_prepared(mock_audit):
    """A PENDING → PREPARED transition writes a PREPARED audit row."""
    session = MagicMock()
    mock_model = MagicMock(status=ModelStatus.PENDING)
    session.get.return_value = mock_model
    user_id = uuid4()
    model_id = uuid4()

    update_model_status(model_id, ModelStatus.PREPARED, session, user_id=user_id)

    mock_audit.assert_called_once()
    audited_model_id, audited_action, audited_user, audited_session = mock_audit.call_args.args
    assert audited_model_id == model_id
    assert audited_user == user_id


@patch("flip_api.model_services.services.model_service.fl_scheduler_service")
@patch("flip_api.model_services.services.model_service.audit_model_action")
def test_update_model_status_notifies_scheduler_on_terminal_status(mock_audit, mock_scheduler):
    """Terminal statuses (ERROR/STOPPED/RESULTS_UPLOADED) prompt the scheduler
    to retire the run — confirms the side-effect on transition.
    """
    session = MagicMock()
    mock_model = MagicMock(status=ModelStatus.RUNNING)
    session.get.return_value = mock_model
    model_id = uuid4()

    update_model_status(model_id, ModelStatus.ERROR, session)

    mock_scheduler.update_fl_scheduler.assert_called_once_with(model_id, session)


@patch("flip_api.model_services.services.model_service.audit_model_action")
def test_update_model_status_no_audit_when_status_is_unchanged(mock_audit):
    """Repeating the same terminal status fires no audit; idempotency keeps the
    timeline clean and prevents duplicate "RESULTS_UPLOADED" rows from polling.
    """
    session = MagicMock()
    mock_model = MagicMock(status=ModelStatus.RESULTS_UPLOADED)
    session.get.return_value = mock_model

    update_model_status(uuid4(), ModelStatus.RESULTS_UPLOADED, session)

    mock_audit.assert_not_called()


# ---------------------------------------------------------------------------
# delete_models — the soft branch (ensure_deletion=False with no rows) returns
# 0 instead of raising. Belt-and-braces for callers that loop over projects.
# ---------------------------------------------------------------------------


def test_delete_models_returns_zero_when_ensure_deletion_is_false():
    session = MagicMock()
    session.exec.return_value.all.return_value = []

    result = delete_models(uuid4(), "user", session, ensure_deletion=False)

    assert result == 0
    # No models → no audit + no commit.
    session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# validate_trust_ids — every id must be in ModelTrustIntersect for the model.
# ---------------------------------------------------------------------------


def test_validate_trust_ids_all_associated():
    session = MagicMock()
    model_id = uuid4()
    trust_1 = uuid4()
    trust_2 = uuid4()
    session.exec.return_value.all.return_value = [trust_1, trust_2]

    assert validate_trust_ids(model_id, [trust_1, trust_2], session) is True


def test_validate_trust_ids_returns_false_when_any_id_is_unknown():
    session = MagicMock()
    trust_1 = uuid4()
    trust_2 = uuid4()
    unknown = uuid4()
    session.exec.return_value.all.return_value = [trust_1, trust_2]

    assert validate_trust_ids(uuid4(), [trust_1, unknown], session) is False


def test_validate_trust_ids_returns_true_for_empty_input():
    """An empty trust-id list is trivially "all associated" — set-difference
    against any superset is empty.
    """
    session = MagicMock()
    session.exec.return_value.all.return_value = [uuid4()]

    assert validate_trust_ids(uuid4(), [], session) is True


def test_get_metrics_orders_points_by_round():
    """Chart points must ascend by round regardless of the row order Postgres returns.

    The query has no inherent ordering, and real runs come back interleaved (an
    observed series: [1, 1, 2, 3, 2, 4, 5, 6, 3, 7, ...]). Plotting that array
    order draws a zig-zag, and "the latest value" reads whichever row happened to
    land last.
    """
    session = MagicMock()
    model_id = uuid4()
    trust_a = uuid4()

    rows = [
        FLMetrics(
            model_id=model_id, trust=trust_a, fl_client_name="T1", label="LOSS",
            global_round=r, x_value=float(r), result=y,
        )
        for r, y in [(3, 0.3), (1, 0.9), (2, 0.5)]
    ]
    session.exec.side_effect = [
        MagicMock(all=MagicMock(return_value=rows)),
        MagicMock(all=MagicMock(return_value=[(trust_a, "GSTT", "Guy's")])),
    ]

    result = get_metrics(model_id, session)

    points = result[0].metrics[0].data
    assert [p.x_value for p in points] == [1, 2, 3]
    assert [p.y_value for p in points] == [0.9, 0.5, 0.3]


def test_run_trusts_by_model_reads_latest_job_roster():
    """Run trusts come from the latest FL job's fl_job_trust rows, not ModelTrustIntersect.

    ModelTrustIntersect gets a row per approved trust at model creation, so sourcing
    it would mark excluded trusts as participants; and a re-initiated model must
    report only its newest job's roster.
    """
    session = MagicMock()
    model_id = uuid4()
    old_job, new_job = uuid4(), uuid4()
    old_trust, new_trust = uuid4(), uuid4()
    older, newer = datetime(2026, 1, 1), datetime(2026, 2, 1)
    session.exec.return_value.all.return_value = [
        (old_job, model_id, older, old_trust, "Old Trust", "OLD"),
        (new_job, model_id, newer, new_trust, "New Trust", "NEW"),
    ]

    result = _run_trusts_by_model([model_id], session)

    stmt = str(session.exec.call_args.args[0])
    assert "fl_job_trust" in stmt
    assert "model_trust_intersect" not in stmt
    assert [(t.id, t.name, t.code) for t in result[model_id]] == [(new_trust, "New Trust", "NEW")]


def test_run_trusts_by_model_created_tie_keeps_one_jobs_roster():
    """Two jobs sharing a created microsecond must not merge rosters (duplicate
    trusts); the tie breaks on job id, matching retrieve_model's ordering."""
    session = MagicMock()
    model_id = uuid4()
    job_a, job_b = sorted([uuid4(), uuid4()])
    trust_id = uuid4()
    created = datetime(2026, 2, 1)
    session.exec.return_value.all.return_value = [
        (job_a, model_id, created, trust_id, "Trust", "TR"),
        (job_b, model_id, created, trust_id, "Trust", "TR"),
    ]

    result = _run_trusts_by_model([model_id], session)

    assert [(t.id, t.name, t.code) for t in result[model_id]] == [(trust_id, "Trust", "TR")]


def test_run_trusts_by_model_empty_input_short_circuits():
    session = MagicMock()

    result = _run_trusts_by_model([], session)

    assert result == {}
    session.exec.assert_not_called()


def test_queued_positions_by_model_ranks_by_scheduler_pickup_order():
    """Positions are the 1-based rank over QUEUED jobs in created order (FIFO)."""
    session = MagicMock()
    first, second = uuid4(), uuid4()
    session.exec.return_value.all.return_value = [first, second]

    assert queued_positions_by_model(session) == {first: 1, second: 2}


def test_queued_positions_by_model_keeps_the_earliest_position_per_model():
    session = MagicMock()
    model_id = uuid4()
    session.exec.return_value.all.return_value = [model_id, model_id]

    assert queued_positions_by_model(session) == {model_id: 1}


def test_queued_positions_by_model_empty_queue():
    session = MagicMock()
    session.exec.return_value.all.return_value = []

    assert queued_positions_by_model(session) == {}
