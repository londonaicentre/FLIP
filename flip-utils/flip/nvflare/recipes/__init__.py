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

"""Pythonic job recipes for FLIP federated learning jobs.

Recipes replace hand-written ``config_fed_server.json`` / ``config_fed_client.json``
templates with a Python class that produces the same JSON layout via ``export()``.
"""

from flip.nvflare.recipes.flip_eval_recipe import FlipEvalRecipe
from flip.nvflare.recipes.flip_fedavg_recipe import FlipFedAvgRecipe

__all__ = ["FlipFedAvgRecipe", "FlipEvalRecipe"]
