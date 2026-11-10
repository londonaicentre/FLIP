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
"""Brace-balanced HCL block extraction for the static guards (no HCL parser needed) (FLIP#1581)."""

from __future__ import annotations


def hcl_block(source: str, header: str, start_at: int = 0) -> str:
    """Return the text of the block starting with `header`, braces included; "" if absent."""
    start = source.find(header, start_at)
    if start < 0:
        return ""
    open_at = source.find("{", start)
    depth = 0
    for index in range(open_at, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    return ""


def hcl_blocks(source: str, header: str) -> list[str]:
    """Every block whose text starts with `header`."""
    blocks, cursor = [], 0
    while (block := hcl_block(source, header, cursor)) != "":
        blocks.append(block)
        cursor = source.find(block, cursor) + len(block)
    return blocks


def resource_blocks(source: str, resource_type: str) -> list[str]:
    """Every `resource "<type>" "<name>"` block in the source."""
    return hcl_blocks(source, f'resource "{resource_type}" ')
