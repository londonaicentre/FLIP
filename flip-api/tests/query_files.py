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
"""Read a ``.sql`` file as the cohort query a project will carry.

Query files keep the repository's licence header, but a project's cohort query is shown verbatim on the
cohort page, so the header is dropped on the way in (FLIP#1746). Only a leading ``--`` comment block that
ends with the licence's last line is removed; everything after it is the query as written.
"""

import re
from pathlib import Path

_LICENCE_HEADER = re.compile(r"\A(?:--[^\n]*\n)*?--\s*limitations under the License\.[^\n]*\n(?:[ \t]*\n)?")


def read_query_file(path: Path) -> str:
    """Return the query in ``path`` without its leading licence header."""
    return _LICENCE_HEADER.sub("", path.read_text(), count=1)
