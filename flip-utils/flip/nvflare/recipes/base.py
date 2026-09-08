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

"""Common base for the FLIP recipes.

Exists for one reason: NVFLARE 2.9.0 renamed ``Recipe.job`` to the private ``Recipe._job``
(``nvflare/recipe/spec.py``) with no public alias. ``recipe.job`` is FLIP's own published
surface — every tutorial's ``job.py`` calls ``stage_app_files(recipe.job)`` — so the rename is
absorbed here rather than pushed out to tutorial authors, and rather than scattering reads of a
private upstream attribute through the recipes.
"""

from nvflare.job_config.api import FedJob
from nvflare.recipe.spec import Recipe


class FlipRecipe(Recipe):
    """A :class:`~nvflare.recipe.spec.Recipe` that keeps ``job`` public.

    Subclass this instead of NVFLARE's ``Recipe`` directly. If a future NVFLARE restores a public
    ``job``, this property becomes redundant but stays harmless — it returns the same object.
    """

    @property
    def job(self) -> FedJob:
        """The recipe's underlying :class:`~nvflare.job_config.api.FedJob`."""
        return self._job
