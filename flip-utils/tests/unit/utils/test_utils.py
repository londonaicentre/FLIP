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

    def test_valid_uuid_v4(self):
        """Should return True for valid UUID v4."""
        assert Utils.is_valid_uuid("123e4567-e89b-12d3-a456-426614174000") is True

    def test_valid_uuid_v4_alternative(self):
        """Should return True for another valid UUID v4."""
        assert Utils.is_valid_uuid("550e8400-e29b-41d4-a716-446655440000") is True

    def test_valid_uuid_uppercase(self):
        """Should return True for uppercase UUID."""
        assert Utils.is_valid_uuid("123E4567-E89B-12D3-A456-426614174000") is True
        assert Utils.is_valid_uuid("550E8400-E29B-41D4-A716-446655440000") is True

    def test_valid_uuid_without_hyphens(self):
        """Should return True for UUID without hyphens."""
        assert Utils.is_valid_uuid("123e4567e89b12d3a456426614174000") is True
        assert Utils.is_valid_uuid("550e8400e29b41d4a716446655440000") is True

    def test_invalid_uuid_too_short(self):
        """Should return False for UUID that is too short."""
        assert Utils.is_valid_uuid("123e4567-e89b-12d3-a456") is False
        assert Utils.is_valid_uuid("550e8400-e29b") is False

    def test_invalid_uuid_random_string(self):
        """Should return False for random strings."""
        assert Utils.is_valid_uuid("not-a-uuid") is False
        assert Utils.is_valid_uuid("hello-world") is False
        assert Utils.is_valid_uuid("model-123") is False

    def test_invalid_uuid_empty_string(self):
        """Should return False for empty string."""
        assert Utils.is_valid_uuid("") is False

    def test_invalid_uuid_none(self):
        """Should return False for None value."""
        assert Utils.is_valid_uuid(None) is False

    def test_invalid_uuid_integer(self):
        """Should return False for integer input."""
        assert Utils.is_valid_uuid(12345) is False

    def test_invalid_uuid_special_characters(self):
        """Should return False for strings with invalid characters."""
        assert Utils.is_valid_uuid("123e4567-e89b-12d3-a456-4266141740zz") is False
        assert Utils.is_valid_uuid("550e8400-ZZZZ-41d4-a716-446655440000") is False


class TestUtilsIsStringEmpty:
    """Test Utils.is_string_empty static method."""

    def test_empty_string(self):
        """Should return True for empty string."""
        assert Utils.is_string_empty("") is True

    def test_whitespace_only(self):
        """Should return True for whitespace-only strings."""
        assert Utils.is_string_empty("   ") is True
        assert Utils.is_string_empty("\t") is True
        assert Utils.is_string_empty("\n") is True
        assert Utils.is_string_empty("  \t\n  ") is True
        assert Utils.is_string_empty(" \t\n ") is True

    def test_non_empty_string(self):
        """Should return False for non-empty strings."""
        assert Utils.is_string_empty("hello") is False
        assert Utils.is_string_empty(" hello ") is False
        assert Utils.is_string_empty("a") is False

    def test_string_with_leading_trailing_whitespace(self):
        """Should return False for strings with content after stripping."""
        assert Utils.is_string_empty("  content  ") is False

    def test_none_value_raises_error(self):
        """Should raise AttributeError for None value."""
        with pytest.raises(AttributeError):
            Utils.is_string_empty(None)
