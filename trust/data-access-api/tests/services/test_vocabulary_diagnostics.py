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
"""
A zero-row cohort caused by a missing OMOP vocabulary must be diagnosable (FLIP#967).

The refusal a trust returns is deliberately indistinguishable: a genuine zero and a
small below-threshold count produce byte-identical responses, so nothing on the wire
can reveal that >=1 patient matched (issue #519). That property is load-bearing and
these tests assert it stays intact.

The cost is that a *misconfigured* trust is equally opaque. The published OMOP
tarballs ship without the vocabulary, and until the separate ~25-minute load runs,
``concept_ancestor`` is empty and every cohort query matches nothing — while
``person`` / ``image_occurrence`` / ``image_feature`` all look healthy. The operator
sees only "no cohort records" from the hub and goes looking at their query or their
imaging import.

So the trust says it in its **own** logs instead. These tests pin that the log fires
exactly when it should, never changes the response, and never runs on the hot path.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data_access_api.routers.schema import CohortQueryInput
from data_access_api.services.cohort import get_statistics

REMEDY = "load-omop-vocab"


@pytest.fixture
def query_input() -> CohortQueryInput:
    """A minimal, valid cohort query input."""
    return CohortQueryInput(
        encrypted_project_id="my_project",
        query_id="1",
        query_name="query_1",
        query="SELECT * FROM omop.image_occurrence",
        trust_id="mock_trust",
    )


def _engine_returning(first_row: object | None) -> MagicMock:
    """Build a mock engine whose vocabulary probe yields ``first_row``.

    Args:
        first_row: What ``.first()`` returns — ``None`` models an empty
            ``concept_ancestor`` (vocabulary absent), a tuple models a loaded one.

    Returns:
        A MagicMock standing in for the SQLAlchemy engine.
    """
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.first.return_value = first_row
    return engine


def test_zero_rows_without_vocabulary_logs_the_remedy(query_input: CohortQueryInput) -> None:
    """The whole point: say what is wrong and how to fix it, trust-side."""
    with (
        patch("data_access_api.services.cohort.engine", _engine_returning(None)),
        patch("data_access_api.services.cohort.logger") as mock_logger,
    ):
        get_statistics(pd.DataFrame(), query_input, threshold=10)

    assert mock_logger.error.called, "a zero-row cohort on a vocabulary-less trust must log ERROR"
    message = mock_logger.error.call_args[0][0]
    assert REMEDY in message, f"the log must name the remedy command. Got:\n{message}"
    assert "concept_ancestor" in message, "name the table checked, so the claim is verifiable"


def test_zero_rows_with_vocabulary_stays_quiet(query_input: CohortQueryInput) -> None:
    """A genuinely empty cohort on a healthy trust is not a misconfiguration."""
    with (
        patch("data_access_api.services.cohort.engine", _engine_returning((1,))),
        patch("data_access_api.services.cohort.logger") as mock_logger,
    ):
        get_statistics(pd.DataFrame(), query_input, threshold=10)

    assert not mock_logger.error.called, (
        "logging a vocabulary problem on a correctly-seeded trust would send the reader "
        "after a seed step they have already run"
    )


def test_below_threshold_but_non_zero_never_probes(query_input: CohortQueryInput) -> None:
    """A 1..threshold-1 cohort proves the vocabulary works — do not touch the database.

    Asserted against the probe itself rather than the engine: the statistics path uses the
    engine legitimately (age/sex distributions), so engine calls are not a proxy for
    "the diagnostic ran".
    """
    with patch("data_access_api.services.cohort._warn_if_vocabulary_missing") as probe:
        get_statistics(pd.DataFrame({"person_id": [1, 2, 3]}), query_input, threshold=10)

    assert not probe.called, "the vocabulary probe must not run for a non-zero cohort"


def test_above_threshold_never_probes(query_input: CohortQueryInput) -> None:
    """The hot path must stay free of the diagnostic query.

    Deliberately no ``person_id`` column: that would send get_statistics down the age/sex
    distribution path, which issues its own SQL and is irrelevant to what is under test.
    """
    with patch("data_access_api.services.cohort._warn_if_vocabulary_missing") as probe:
        stats = get_statistics(
            pd.DataFrame({"image_occurrence_id": list(range(50))}), query_input, threshold=10
        )

    assert stats.suppressed is False
    assert not probe.called, "a healthy result must not pay for the diagnostic"


def test_response_is_identical_whether_or_not_the_vocabulary_is_loaded(
    query_input: CohortQueryInput,
) -> None:
    """The privacy invariant: the diagnosis is log-only and never reaches the wire.

    A zero-row cohort must serialise identically on a vocabulary-less trust and a
    healthy one — otherwise the log fix would have opened the very side channel the
    suppression exists to close.
    """
    with patch("data_access_api.services.cohort.engine", _engine_returning(None)):
        without_vocabulary = get_statistics(pd.DataFrame(), query_input, threshold=10)
    with patch("data_access_api.services.cohort.engine", _engine_returning((1,))):
        with_vocabulary = get_statistics(pd.DataFrame(), query_input, threshold=10)

    assert without_vocabulary.model_dump(exclude={"created"}) == with_vocabulary.model_dump(
        exclude={"created"}
    )
    assert without_vocabulary.record_count == 0
    assert without_vocabulary.suppressed is True


def test_probe_failure_does_not_break_the_response(query_input: CohortQueryInput) -> None:
    """Best-effort: a broken diagnostic must never turn a valid answer into an error."""
    from sqlalchemy.exc import SQLAlchemyError

    engine = MagicMock()
    engine.connect.side_effect = SQLAlchemyError("connection pool exhausted")

    with (
        patch("data_access_api.services.cohort.engine", engine),
        patch("data_access_api.services.cohort.logger") as mock_logger,
    ):
        stats = get_statistics(pd.DataFrame(), query_input, threshold=10)

    assert stats.record_count == 0
    assert stats.suppressed is True
    assert not mock_logger.error.called, "an unusable probe proves nothing — do not claim it does"
