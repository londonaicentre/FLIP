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

import pytest
from pydantic import ValidationError

from data_access_api.config import Settings

# Deliberately a literal rather than an import of ``config.DEFAULT_COHORT_QUERY_THRESHOLD``:
# these tests pin the shipped value independently of its source, so importing the constant
# would make ``test_cohort_query_threshold_defaults_to_ten`` tautological.
DEFAULT_COHORT_QUERY_THRESHOLD = 10


def test_cohort_query_threshold_defaults_to_ten():
    """The shipped disclosure floor. Kept explicit so a change to it is a deliberate edit."""
    assert Settings().COHORT_QUERY_THRESHOLD == DEFAULT_COHORT_QUERY_THRESHOLD


@pytest.mark.parametrize("value", ["", None])
def test_cohort_query_threshold_coerces_empty_to_default(value):
    """An empty value must fall back to the default rather than fail validation.

    The service Makefile exports kit-file names with ``sed 's/=.*//'``, stripping the value
    from every line including commented ones — so a commented-out entry reaches the process
    as an empty string. Without coercion pydantic rejects it against ``int`` at import,
    which takes down the service and every test that imports it.
    """
    assert Settings(COHORT_QUERY_THRESHOLD=value).COHORT_QUERY_THRESHOLD == DEFAULT_COHORT_QUERY_THRESHOLD


def test_cohort_query_threshold_empty_coercion_tracks_field_default():
    """The empty-string coercion must yield whatever the field default is, not a copy of it.

    Regression test for the drift the validator used to carry: it returned a hard-coded ``10``
    alongside a field default of ``10``, so changing one silently left the other behind. Both
    now read ``DEFAULT_COHORT_QUERY_THRESHOLD``, and re-introducing a literal fails this.
    """
    assert Settings(COHORT_QUERY_THRESHOLD="").COHORT_QUERY_THRESHOLD == Settings().COHORT_QUERY_THRESHOLD


def test_cohort_query_threshold_accepts_operator_override():
    """A trust may raise its own floor; the value still parses from a string as env vars do."""
    assert Settings(COHORT_QUERY_THRESHOLD="25").COHORT_QUERY_THRESHOLD == 25


@pytest.mark.parametrize("value", [0, -1, "0", "-5"])
def test_cohort_query_threshold_rejects_non_positive(value):
    """A non-positive threshold must fail loudly rather than disable the control.

    ``COHORT_QUERY_THRESHOLD=0`` would turn off every check that reads it at once: both
    row-level gates (``len(df) < 0`` is never true, so ``/cohort/dataframe`` and
    ``/cohort/accession-ids`` would release any cohort, including a single patient) and the
    statistics suppression on ``/cohort``. Settings are built at import, so rejecting here
    means the service refuses to start instead of running with the floor silently removed.
    """
    with pytest.raises(ValidationError):
        Settings(COHORT_QUERY_THRESHOLD=value)
