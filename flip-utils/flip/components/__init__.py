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

"""
FLIP Components module containing reusable FL components.

Components include event handlers, model locators, JSON generators, and persistence utilities.

Exports:
    - ClientEventHandler: Client-side event handler
    - ServerEventHandler: Server-side event handler
    - PTModelLocator: PyTorch model locator
    - ValidationJsonGenerator: Validation results JSON generator
    - EvaluationJsonGenerator: Evaluation results JSON generator
    - PersistToS3AndCleanup: S3 persistence and cleanup component
    - PercentilePrivacy: Percentile-based privacy filter
    - CleanupImages: Image cleanup executor
"""

from flip.components.cleanup import CleanupImages
from flip.components.custom_percentile_privacy import PercentilePrivacy
from flip.components.evaluation_json_generator import EvaluationJsonGenerator
from flip.components.flip_client_event_handler import ClientEventHandler
from flip.components.flip_server_event_handler import ServerEventHandler
from flip.components.persist_and_cleanup import PersistToS3AndCleanup
from flip.components.pt_model_locator import PTModelLocator
from flip.components.validation_json_generator import ValidationJsonGenerator

__all__ = [
    "ClientEventHandler",
    "ServerEventHandler",
    "PTModelLocator",
    "ValidationJsonGenerator",
    "EvaluationJsonGenerator",
    "PersistToS3AndCleanup",
    "PercentilePrivacy",
    "CleanupImages",
]
