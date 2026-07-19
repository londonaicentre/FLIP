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
FLIP Factory.

This module provides the FLIP() factory function that returns the appropriate
FLIP implementation for the current environment.
"""

from flip.constants.flip_constants import FlipConstants
from flip.core.base import FLIPBase


def FLIP() -> FLIPBase:
    """
    Factory function to create a FLIP instance for the current environment.

    The implementation is chosen by environment alone: ``FLIPStandardDev`` under ``LOCAL_DEV``,
    ``FLIPStandardProd`` otherwise.

    Returns:
        FLIPBase: A FLIP instance appropriate to the environment.

    Examples:
        .. code-block:: python

            from flip import FLIP

            flip = FLIP()
            df = flip.get_dataframe(project_id, query)
    """
    from flip.core.standard import FLIPStandardDev, FLIPStandardProd

    return FLIPStandardDev() if FlipConstants.LOCAL_DEV else FLIPStandardProd()
