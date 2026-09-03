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

from fl_api.config import Settings


def test_coerce_empty_per_job_fl_server_truthy():
    """PER_JOB_FL_SERVER accepts the same truthy spellings in fl-api-base and flip-api."""
    for value in ("true", "1", "yes", "on", "True", "YES"):
        assert Settings.coerce_empty_per_job_fl_server(value) is True, value


def test_coerce_empty_per_job_fl_server_falsy():
    """Empty/commented .env lines and every falsy spelling mean False."""
    for value in ("", "false", "0", "no", "off", "FALSE", None):
        assert Settings.coerce_empty_per_job_fl_server(value) is False, value


def test_coerce_empty_per_job_fl_server_passthrough_bool():
    assert Settings.coerce_empty_per_job_fl_server(True) is True
    assert Settings.coerce_empty_per_job_fl_server(False) is False
