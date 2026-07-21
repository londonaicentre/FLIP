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

"""Tests for the flip.schemas request schemas."""

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

import flip.schemas
from flip.schemas import FLLogEvent, TrainingLog, TrainingMetrics

_REPO_ROOT = Path(__file__).resolve().parents[3]


class TestTrainingMetrics:
    """Test the TrainingMetrics request schema."""

    def test_model_dump_uses_snake_case_contract(self):
        """model_dump should emit the snake_case field names expected by the hub."""
        payload = TrainingMetrics(
            fl_client_name="site-1",
            global_round=3,
            label="LOSS_FUNCTION",
            result=0.42,
        ).model_dump()

        assert payload == {
            "fl_client_name": "site-1",
            "global_round": 3,
            "label": "LOSS_FUNCTION",
            "result": 0.42,
        }

    def test_global_round_accepts_zero(self):
        """global_round of 0 is the first round and must be valid."""
        metrics = TrainingMetrics(fl_client_name="site-1", global_round=0, label="loss", result=1.0)
        assert metrics.global_round == 0

    def test_global_round_rejects_negative(self):
        """global_round below 0 should raise a ValidationError."""
        with pytest.raises(ValidationError):
            TrainingMetrics(fl_client_name="site-1", global_round=-1, label="loss", result=1.0)

    def test_result_coerces_int_to_float(self):
        """An integer result should be coerced to a float."""
        metrics = TrainingMetrics(fl_client_name="site-1", global_round=1, label="loss", result=1)
        assert isinstance(metrics.result, float)
        assert metrics.result == 1.0

    def test_missing_field_raises(self):
        """Omitting a required field should raise a ValidationError."""
        with pytest.raises(ValidationError):
            TrainingMetrics.model_validate({"fl_client_name": "site-1", "global_round": 1, "label": "loss"})


class TestTrainingLog:
    """Test the TrainingLog request schema (mirrors flip-api's domain/schemas/private.py)."""

    def test_model_dump_uses_snake_case_contract(self):
        """model_dump should emit the snake_case field names expected by the hub."""
        payload = TrainingLog(fl_client_name="site-1", log="handled exception").model_dump()

        assert payload == {
            "fl_client_name": "site-1",
            "log": "handled exception",
            "event_type": None,
            "global_round": None,
            "details": None,
            "success": True,
        }

    def test_event_shape_validates_without_log_text(self):
        """Typed events carry facts, never display text — wording lives hub-side."""
        payload = TrainingLog(
            event_type=FLLogEvent.ROUND_STARTED,
            global_round=1,
            details={"total_rounds": 15},
        )
        assert payload.log is None
        assert payload.fl_client_name is None

    def test_rejects_neither_log_nor_event(self):
        with pytest.raises(ValidationError):
            TrainingLog.model_validate({"fl_client_name": "site-1"})

    def test_rejects_both_log_and_event(self):
        with pytest.raises(ValidationError):
            TrainingLog(fl_client_name="site-1", log="text", event_type=FLLogEvent.ROUND_STARTED, global_round=1)

    def test_event_requires_global_round(self):
        with pytest.raises(ValidationError):
            TrainingLog(event_type=FLLogEvent.ROUND_AGGREGATED)

    def test_global_round_is_one_based(self):
        """Events are normalised to 1-based rounds on both backends before sending."""
        with pytest.raises(ValidationError):
            TrainingLog(event_type=FLLogEvent.ROUND_STARTED, global_round=0)

    def test_global_round_above_pg_integer_max_is_rejected(self):
        """The hub's fl_logs.global_round column is PG INTEGER: an oversized round
        must fail validation sender-side rather than 500 at insert hub-side."""
        with pytest.raises(ValidationError):
            TrainingLog(event_type=FLLogEvent.ROUND_STARTED, global_round=2**31)

    def test_global_round_at_pg_integer_max_is_accepted(self):
        payload = TrainingLog(event_type=FLLogEvent.ROUND_STARTED, global_round=2**31 - 1)
        assert payload.global_round == 2**31 - 1

    def test_unknown_event_type_is_accepted_for_forward_compat(self):
        """The vocabulary is plain text end-to-end (mirrors flip-api): a newer
        vocabulary member must serialise without a schema change here."""
        payload = TrainingLog(event_type="SOMETHING_NEW", global_round=3)
        assert payload.event_type == "SOMETHING_NEW"

    @pytest.mark.parametrize("event_type", ["", "   "])
    def test_blank_event_type_is_rejected(self, event_type):
        with pytest.raises(ValidationError):
            TrainingLog(event_type=event_type, global_round=3)

    def test_queue_position_is_rejected(self):
        """QUEUE_POSITION is reserved for the hub's own FL scheduler (mirrors
        flip-api, which 422-rejects it at ingest) — refusing it here fails fast
        FL-side instead of after the POST."""
        with pytest.raises(ValidationError, match="QUEUE_POSITION"):
            TrainingLog(event_type="QUEUE_POSITION", global_round=1)

    def test_missing_field_raises(self):
        """Omitting a required field should raise a ValidationError."""
        with pytest.raises(ValidationError):
            TrainingLog.model_validate({"fl_client_name": "site-1"})


_HUB_RESERVED_EVENT = "QUEUE_POSITION"


class _StripHubReservedEvent(ast.NodeTransformer):
    """Drop statements that reference the hub-reserved QUEUE_POSITION event.

    The QUEUE_POSITION reservation is the one sanctioned mirror asymmetry: the hub
    declares the enum member and rejects it at ingest, while the FL side omits the
    member and rejects it at send — with direction-appropriate wording on each side.
    Those statements are excluded from the AST comparison and pinned by shape in
    ``test_queue_position_stays_hub_reserved`` instead.
    """

    def visit_Assign(self, node: ast.Assign) -> ast.Assign | None:
        if any(isinstance(target, ast.Name) and target.id == _HUB_RESERVED_EVENT for target in node.targets):
            return None
        return node

    def visit_If(self, node: ast.If) -> ast.AST | None:
        if _HUB_RESERVED_EVENT in ast.dump(node.test):
            return None
        return self.generic_visit(node)


def _class_def(path: Path, class_name: str) -> ast.ClassDef:
    """Return the AST of a top-level class definition in a source file.

    Args:
        path (Path): The Python source file to parse.
        class_name (str): The top-level class to extract.

    Returns:
        ast.ClassDef: The class definition node.
    """
    module = ast.parse(path.read_text())
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise AssertionError(f"class {class_name} not found in {path}")


def _class_body_ast(path: Path, class_name: str) -> list[str]:
    """Return the AST dump of each statement in a class body, docstring excluded.

    Docstrings and comments may legitimately differ between the two mirrors;
    field definitions, constraints and validators may not — except the
    hub-reserved QUEUE_POSITION statements, which are stripped from both sides
    (see ``_StripHubReservedEvent``).

    Args:
        path (Path): The Python source file to parse.
        class_name (str): The top-level class whose body to extract.

    Returns:
        list[str]: One ``ast.dump`` string per non-docstring body statement.
    """
    body = _class_def(path, class_name).body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    stripped = (_StripHubReservedEvent().visit(stmt) for stmt in body)
    return [ast.dump(stmt) for stmt in stripped if stmt is not None]


class TestHubMirrorStaysInSync:
    """Pin the FL-side schemas to their flip-api mirrors.

    The hub validates against its own copies of these definitions (kept in sync
    by hand — the two packages share no code). Drift would surface only as 422s
    silently swallowed by the best-effort senders; this fails the build instead.
    Compared at the AST level so docstrings and comments stay free to differ. The
    hub-reserved QUEUE_POSITION statements are the one sanctioned asymmetry —
    excluded from the comparison and pinned separately below.
    """

    @pytest.mark.parametrize(
        ("class_name", "hub_path"),
        [
            ("TrainingLog", "flip-api/src/flip_api/domain/schemas/private.py"),
            ("TrainingMetrics", "flip-api/src/flip_api/domain/schemas/private.py"),
            ("FLLogEvent", "flip-api/src/flip_api/domain/schemas/types.py"),
        ],
    )
    def test_definition_matches_flip_api_mirror(self, class_name, hub_path):
        fl_side = _class_body_ast(Path(flip.schemas.__file__), class_name)
        hub_side = _class_body_ast(_REPO_ROOT / hub_path, class_name)
        assert fl_side == hub_side, f"{class_name} drifted from its flip-api mirror ({hub_path}) — update both together"

    def test_queue_position_stays_hub_reserved(self):
        """Pin the shape of the one sanctioned mirror divergence.

        The hub's FLLogEvent must be exactly the FL-side vocabulary plus the
        hub-emitted QUEUE_POSITION, which this package deliberately omits — FL
        images must never send it. Each side's validator rejection of the event
        is pinned behaviourally by that side's own tests.
        """
        hub_enum = _class_def(_REPO_ROOT / "flip-api/src/flip_api/domain/schemas/types.py", "FLLogEvent")
        hub_members = [
            target.id
            for stmt in hub_enum.body
            if isinstance(stmt, ast.Assign)
            for target in stmt.targets
            if isinstance(target, ast.Name)
        ]
        assert hub_members == [*[event.name for event in FLLogEvent], _HUB_RESERVED_EVENT]
