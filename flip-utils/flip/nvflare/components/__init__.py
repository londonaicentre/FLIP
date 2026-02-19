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
FLIP Components module containing reusable FL components.

Components include event handlers, model locators, JSON generators, and persistence utilities.

Exports:
    - ClientEventHandler: Client-side event handler
    - ServerEventHandler: Server-side event handler
    - PTModelLocator: PyTorch model locator
    - InitialPTModelLocator: PyTorch model locator for initial models with safehouse fallback
    - EvaluationPTModelLocator: PyTorch model locator for evaluation workflows
    - ValidationJsonGenerator: Validation results JSON generator
    - EvaluationJsonGenerator: Evaluation results JSON generator
    - PersistToS3AndCleanup: S3 persistence and cleanup component
    - PercentilePrivacy: Percentile-based privacy filter
    - StagePercentilePrivacy: Stage-aware percentile-based privacy filter
    - CleanupImages: Image cleanup executor
"""

from flip.nvflare.components.cleanup import CleanupImages
from flip.nvflare.components.custom_percentile_privacy import PercentilePrivacy
from flip.nvflare.components.evaluation_json_generator import \
    EvaluationJsonGenerator
from flip.nvflare.components.flip_client_event_handler import \
    ClientEventHandler
from flip.nvflare.components.flip_server_event_handler import \
    ServerEventHandler
from flip.nvflare.components.persist_and_cleanup import PersistToS3AndCleanup
from flip.nvflare.components.pt_model_locator import (EvaluationPTModelLocator,
                                                      InitialPTModelLocator,
                                                      PTModelLocator)
from flip.nvflare.components.stage_percentile_privacy import \
    StagePercentilePrivacy
from flip.nvflare.components.validation_json_generator import \
    ValidationJsonGenerator

__all__ = [
    "ClientEventHandler",
    "ServerEventHandler",
    "PTModelLocator",
    "InitialPTModelLocator",
    "EvaluationPTModelLocator",
    "ValidationJsonGenerator",
    "EvaluationJsonGenerator",
    "PersistToS3AndCleanup",
    "PercentilePrivacy",
    "StagePercentilePrivacy",
    "CleanupImages",
    "ValidationJsonGenerator",
    "EvaluationJsonGenerator",
    "PersistToS3AndCleanup",
    "PercentilePrivacy",
    "StagePercentilePrivacy",
    "CleanupImages",
]
