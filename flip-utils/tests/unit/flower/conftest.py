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

"""Fixtures shared by the Flower unit tests."""

import pytest
from flwr.supercore.task_identity import TaskIdentity


@pytest.fixture(autouse=True)
def task_identity():
    """Give the process the task identity flwr's runtime would have set.

    Since flwr 1.38, constructing an instruction ``Message`` (what ``FedAvg.configure_*``
    and the tests' own helpers do) stamps ``run_id`` / ``src_node_id`` / ``src_task_id``
    from the process-wide ``TaskIdentity``, which the SuperLink / SuperNode task process
    sets before any app code runs. A bare test process has none, so every such
    construction raised ``RuntimeError: TaskIdentity.run_id is not set``.
    """
    TaskIdentity.task_id = 1
    TaskIdentity.run_id = 1
    TaskIdentity.node_id = 0
    yield
    TaskIdentity._task_id = None
    TaskIdentity._run_id = None
    TaskIdentity._node_id = None
