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

"""Timestamp helpers.

``datetime.utcnow()`` is deprecated from Python 3.12 and — the reason it matters
here — returns a **naive** datetime. Naive and aware datetimes cannot be compared
or subtracted, so mixing the two raises ``TypeError`` at runtime, and the mix
previously forced defensive ``if dt.tzinfo is None`` normalisation at read sites.

:func:`utc_now` is the single source of "now" for the application: always
timezone-aware, always UTC. Paired with ``TIMESTAMP WITH TIME ZONE`` columns
(see :data:`UTC_DATETIME`), values stay aware end to end — written aware, read
back aware — so no call site needs to normalise.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime

#: Column type for every persisted timestamp: Postgres ``TIMESTAMP WITH TIME ZONE``.
#: SQLAlchemy returns aware datetimes for these columns, which is what keeps
#: read-back values comparable with :func:`utc_now`.
#:
#: Typed ``Any`` because SQLModel annotates ``Field(sa_type=...)`` as ``type[Any]``
#: while accepting a configured type *instance* (which is the only way to pass
#: ``timezone=True``); without this every field would need its own ``type: ignore``.
UTC_DATETIME: Any = DateTime(timezone=True)


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Use instead of the deprecated, naive ``datetime.utcnow()``.

    Returns:
        datetime: The current UTC time, with ``tzinfo=timezone.utc``.
    """
    return datetime.now(timezone.utc)
