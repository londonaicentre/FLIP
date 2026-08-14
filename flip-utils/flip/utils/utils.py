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

"""Utility functions for FLIP."""

import hashlib
from typing import Any
from uuid import UUID


class Utils:
    """Utility class with static helper methods."""

    @staticmethod
    def is_valid_uuid(val: Any) -> bool:
        """
        Check if a value is a valid UUID.

        Args:
            val: Value to check (will be converted to string)

        Returns:
            bool: True if valid UUID, False otherwise
        """
        try:
            UUID(str(val))
            return True
        except ValueError:
            return False

    @staticmethod
    def is_string_empty(val: str) -> bool:
        """
        Check if a string is empty or contains only whitespace.

        Args:
            val: String to check

        Returns:
            bool: True if empty or whitespace-only, False otherwise
        """
        return val.strip() == ""

    @staticmethod
    def hash_for_log(value: Any) -> str:
        """
        Return a short, stable fingerprint of a sensitive value for log correlation.

        Accession numbers are patient-level linkage identifiers and download paths
        are named after them, so the platform logging policy (FLIP docs,
        sys-admin "Logging policy") keeps them out of logs — including the FL run
        logs this package writes, which can leave the trust. Log this fingerprint
        instead: the SHA-256 of the whitespace-normalised, lower-cased value,
        truncated to 12 hex chars. Anyone holding the original value (e.g. a model
        developer with their cohort's accession list) can re-derive it to find
        matching log lines.

        Args:
            value: The sensitive value (will be converted to string).

        Returns:
            str: A 12-hex-char fingerprint of the value.
        """
        normalised = " ".join(str(value).strip().lower().split())
        return hashlib.sha256(normalised.encode()).hexdigest()[:12]
