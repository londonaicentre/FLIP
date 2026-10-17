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
"""Every HTTP call imaging-api makes passes a timeout (FLIP#1521).

imaging-api runs one uvicorn worker, so a call to a wedged XNAT without a timeout hangs the whole
service rather than failing.
"""

import ast
from pathlib import Path

import imaging_api

_PACKAGE = Path(imaging_api.__file__).parent
_VERBS = {"get", "put", "post", "delete", "patch", "head", "request"}


def _calls_without_timeout() -> list[str]:
    missing = []
    for path in sorted(_PACKAGE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "requests"
                and node.func.attr in _VERBS
                and "timeout" not in {kw.arg for kw in node.keywords}
            ):
                missing.append(f"{path.relative_to(_PACKAGE.parent)}:{node.lineno}")
    return missing


def test_every_requests_call_passes_a_timeout() -> None:
    missing = _calls_without_timeout()
    assert not missing, f"requests calls without timeout= (XNAT_REQUEST_TIMEOUT or XNAT_TRANSFER_TIMEOUT): {missing}"
