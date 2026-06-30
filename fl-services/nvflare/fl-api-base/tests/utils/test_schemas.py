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

"""Guard against ``fl_api.utils.schemas.TargetType`` drifting from NVFLARE's own.

NVFLARE 2.8.0 removed the ``(str, Enum)`` ``TargetType`` that the fl-api routers
used to import from ``nvflare.fuel.hci.client.fl_admin_api``; its successor,
``nvflare.fuel.flare_api.api_spec.TargetType``, is a plain class FastAPI rejects
as a path-param annotation. We therefore re-declare ``TargetType`` locally as a
``StrEnum`` (see ``fl_api/utils/schemas.py``). Because that copy is hand-maintained,
this test fails loudly if a future NVFLARE bump adds, removes, or renames a member,
prompting us to re-sync the local enum.
"""

from enum import Enum

from nvflare.fuel.flare_api.api_spec import TargetType as NVFlareTargetType

from fl_api.utils.schemas import TargetType


def _values(target_type_cls: type) -> set[str]:
    """Return the set of string target values a TargetType class exposes.

    Handles both our local ``StrEnum`` (iterate members) and NVFLARE's plain class
    (collect upper-case string class attributes), so the two can be compared
    regardless of how each declares its members.

    Args:
        target_type_cls (type): an enum or plain class declaring ALL/SERVER/CLIENT.

    Returns:
        set[str]: the target string values (e.g. ``{"all", "server", "client"}``).
    """
    if issubclass(target_type_cls, Enum):
        return {item.value for item in target_type_cls}

    return {value for name, value in vars(target_type_cls).items() if name.isupper() and isinstance(value, str)}


def test_target_type_matches_nvflare_target_type() -> None:
    assert _values(TargetType) == _values(NVFlareTargetType), (
        "fl_api.utils.schemas.TargetType is out of sync with "
        "nvflare.fuel.flare_api.api_spec.TargetType — re-sync the local StrEnum "
        "after this NVFLARE upgrade."
    )
