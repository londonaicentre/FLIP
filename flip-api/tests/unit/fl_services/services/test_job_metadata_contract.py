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

from flip_api.domain.schemas.status import FLJobStatus


def test_fl_job_status_has_exactly_five_contract_values():
    assert {s.value for s in FLJobStatus} == {"PENDING", "RUNNING", "FINISHED", "FAILED", "STOPPED"}
