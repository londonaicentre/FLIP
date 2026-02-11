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

from flip.constants import FlipConstants


class PTConstants:
    PTServerName = "server"
    PTModelName = "model.pt"
    PTFileModelName = "FL_global_model.pt"
    PTLocalModelName = "local_model.pt"
    PTModelsDir = "models" if not FlipConstants.LOCAL_DEV else ""
    EvalResultsFilename = "evaluation_results.json"
    EvalDir = "evaluation_results"
    EvalTaskName = "evaluation"
