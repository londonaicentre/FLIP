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

"""Job type definitions for FLIP."""

from enum import Enum
from typing import Literal


class JobType(str, Enum):
    """Enumeration of supported FLIP job types."""

    STANDARD = "standard"
    EVALUATION = "evaluation"
    FED_OPT = "fed_opt"
    DIFFUSION = "diffusion_model"


# Type alias for job type string literals
JobTypeStr = Literal["standard", "evaluation", "fed_opt", "diffusion_model"]
