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

"""Percent-encoding for values interpolated into XNAT request URLs.

Every XNAT call in this service is built by f-string interpolation of identifiers —
project ids, subject ids, experiment labels, scan and resource ids, usernames,
filenames. Dropped in raw, a value carrying ``?``, ``#`` or ``%`` silently changes
what the request means: ``?`` starts a query string (so a caller chooses XNAT query
parameters), ``#`` truncates everything after it, and a stray ``%`` corrupts the
encoding. ``/`` adds path segments and reaches a different XNAT resource entirely.

That matters more here than the values' provenance suggests. imaging-api authenticates
to XNAT with a privileged service account, so steering one of its URLs is a way to make
a privileged XNAT call the caller could not make directly.

FastAPI blocks the ``/`` case for path parameters only — ASGI percent-decodes the path
before routing and the default converter is ``[^/]+``, so ``%2F`` fails to match the
route rather than slipping through. Nothing protects the other characters, and nothing
protects values that arrive in a request body or are read back out of another system:
``subject_id`` is parsed from an XNAT response and accession numbers come from OMOP.
"""

from urllib.parse import quote


def seg(value: object) -> str:
    """Percent-encode ``value`` for use as a single URL path or query segment.

    ``safe=""`` is the point: ``urllib.parse.quote`` defaults to ``safe="/"``, which
    leaves the one character most able to change which resource a URL addresses. Baking
    the argument in here means a call site cannot forget it.

    Args:
        value (object): The value to interpolate. Coerced with ``str`` first, so ints
            and ``IntEnum`` members can be passed directly.

    Returns:
        str: The percent-encoded value, safe to interpolate into a URL.
    """
    return quote(str(value), safe="")
