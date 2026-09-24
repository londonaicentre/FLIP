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

"""Trust governance policy for the site plane (FLIP#1259).

Public surface: :func:`load_policy` at startup, :func:`decide` at each gated call site.
"""

from data_access_api.policy.decide import decide
from data_access_api.policy.loader import load_policy, parse_policy
from data_access_api.policy.model import (
    ACTION_COHORT_ACCESSION_IDS,
    ACTION_COHORT_DATAFRAME,
    ACTION_COHORT_STATISTICS,
    KNOWN_ACTIONS,
    KNOWN_SECTIONS,
    AccessPolicyError,
    Decision,
    Policy,
    Rule,
    subject_attributes,
)

__all__ = [
    "ACTION_COHORT_ACCESSION_IDS",
    "ACTION_COHORT_DATAFRAME",
    "ACTION_COHORT_STATISTICS",
    "KNOWN_ACTIONS",
    "KNOWN_SECTIONS",
    "AccessPolicyError",
    "Decision",
    "Policy",
    "Rule",
    "decide",
    "load_policy",
    "parse_policy",
    "subject_attributes",
]
