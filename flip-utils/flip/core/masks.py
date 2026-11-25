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

"""The labels.json that names a label mask's values, for flip.add_mask (FLIP#1945).

XNAT's nifti_to_seg command (trust/xnat/nifti-to-seg/) reads it to turn the mask into a DICOM-SEG, so it is
checked here, where the job can still fix it, rather than failing a container at the trust.
"""

from typing import Any

LABELS_FILE = "labels.json"
# DICOM-SEG segment numbers are 16-bit; far fewer structures than this is the realistic range.
MAX_LABELS = 1000
_CODE_KEYS = ("category", "type", "modifier")
_KEYS = {"name", "color", "algorithm", *_CODE_KEYS}


def labels_json(labels: dict[int, str | dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The labels.json content for ``labels``: each value keyed by its decimal string.

    Args:
        labels (dict[int, str | dict[str, Any]]): Each label value (1-65535) to its name, or to a dict with
            ``name`` and optionally ``category``, ``type``, ``modifier`` (``[code, scheme, meaning]``),
            ``color`` (``[r, g, b]``) and ``algorithm``.

    Returns:
        dict[str, dict[str, Any]]: The entries, ready to write as JSON.

    Raises:
        ValueError: If ``labels`` is empty or too long, or an entry is malformed.
    """
    if not labels or len(labels) > MAX_LABELS:
        raise ValueError(f"expect 1 to {MAX_LABELS} labels, got {len(labels)}")
    entries: dict[str, dict[str, Any]] = {}
    for value, entry in labels.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
            raise ValueError(f"label values are integers from 1 to 65535 (0 is background), got {value!r}")
        entry = {"name": entry} if isinstance(entry, str) else dict(entry)
        if not isinstance(entry.get("name"), str) or not entry["name"]:
            raise ValueError(f"label {value} needs a name")
        if unknown := set(entry) - _KEYS:
            raise ValueError(f"label {value} has unknown keys {sorted(unknown)}")
        for key in _CODE_KEYS:
            code = entry.get(key)
            if code is not None and not (
                isinstance(code, list | tuple) and len(code) == 3 and all(isinstance(c, str) for c in code)
            ):
                raise ValueError(f"label {value} {key} must be [code, scheme, meaning], got {code!r}")
        color = entry.get("color")
        rgb = isinstance(color, list | tuple) and len(color) == 3
        if color is not None and not (rgb and all(isinstance(c, int) and 0 <= c <= 255 for c in color)):
            raise ValueError(f"label {value} color must be [r, g, b] in 0-255, got {color!r}")
        entries[str(value)] = {k: list(v) if isinstance(v, tuple) else v for k, v in entry.items()}
    return entries
