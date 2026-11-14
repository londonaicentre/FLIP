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

"""Tests for flip.utils.utils module."""

import pytest

from flip.utils.utils import Utils


class TestUtilsIsValidUuid:
    """Test Utils.is_valid_uuid static method."""

    @pytest.mark.parametrize(
        "value",
        [
            "123e4567-e89b-12d3-a456-426614174000",
            "550e8400-e29b-41d4-a716-446655440000",
            "123E4567-E89B-12D3-A456-426614174000",  # uppercase
            "550E8400-E29B-41D4-A716-446655440000",
            "123e4567e89b12d3a456426614174000",  # without hyphens  # pragma: allowlist secret
            "550e8400e29b41d4a716446655440000",  # pragma: allowlist secret
        ],
    )
    def test_valid(self, value):
        """Should return True for valid UUIDs, any case, with or without hyphens."""
        assert Utils.is_valid_uuid(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "123e4567-e89b-12d3-a456",  # too short
            "550e8400-e29b",
            "not-a-uuid",  # random strings
            "hello-world",
            "model-123",
            "",
            None,
            12345,
            "123e4567-e89b-12d3-a456-4266141740zz",  # invalid characters
            "550e8400-ZZZZ-41d4-a716-446655440000",
        ],
    )
    def test_invalid(self, value):
        """Should return False for anything that is not a UUID."""
        assert Utils.is_valid_uuid(value) is False


class TestUtilsIsStringEmpty:
    """Test Utils.is_string_empty static method."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("", True),
            ("   ", True),
            ("\t", True),
            ("\n", True),
            ("  \t\n  ", True),
            (" \t\n ", True),
            ("hello", False),
            (" hello ", False),
            ("a", False),
            ("  content  ", False),
        ],
    )
    def test_is_string_empty(self, value, expected):
        """True for empty or whitespace-only strings, False once anything is left after stripping."""
        assert Utils.is_string_empty(value) is expected

    def test_none_value_raises_error(self):
        """Should raise AttributeError for None value."""
        with pytest.raises(AttributeError):
            Utils.is_string_empty(None)
