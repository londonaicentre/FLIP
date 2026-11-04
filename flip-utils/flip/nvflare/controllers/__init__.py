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
FLIP Controllers module containing FLARE workflow controllers.

Controllers orchestrate federated learning workflows.

Exports:
    - InitTraining: Initialization controller for training setup
    - ScatterAndGather: Main training loop controller with FedAvg aggregation
    - BroadcastTask: Broadcast a lifecycle task and wait for client responses
    - InitEvaluation: Initialization controller for evaluation setup
"""

from flip.nvflare.controllers.broadcast_task import BroadcastTask
from flip.nvflare.controllers.init_evaluation import InitEvaluation
from flip.nvflare.controllers.init_training import InitTraining
from flip.nvflare.controllers.scatter_and_gather import ScatterAndGather

__all__ = [
    "InitTraining",
    "ScatterAndGather",
    "BroadcastTask",
    "InitEvaluation",
]
