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

"""Tests for flip.utils.utils module."""

import pytest


class TestUtilsIsValidUuid:
    """Test the is_valid_uuid utility function."""

    def test_valid_uuid_string(self):
        """Should return True for valid UUID string."""
        from flip.utils import Utils

        valid_uuid = "550e8400-e29b-41d4-a716-446655440000"
        assert Utils.is_valid_uuid(valid_uuid) is True

    def test_valid_uuid_with_uppercase(self):
        """Should return True for UUID with uppercase letters."""
        from flip.utils import Utils

        valid_uuid = "550E8400-E29B-41D4-A716-446655440000"
        assert Utils.is_valid_uuid(valid_uuid) is True

    def test_valid_uuid_without_hyphens(self):
        """Should return True for UUID without hyphens."""
        from flip.utils import Utils

        valid_uuid = "550e8400e29b41d4a716446655440000"
        assert Utils.is_valid_uuid(valid_uuid) is True

    def test_invalid_uuid_string(self):
        """Should return False for invalid UUID string."""
        from flip.utils import Utils

        invalid_uuid = "model-123"
        assert Utils.is_valid_uuid(invalid_uuid) is False

    def test_invalid_uuid_empty_string(self):
        """Should return False for empty string."""
        from flip.utils import Utils

        assert Utils.is_valid_uuid("") is False

    def test_invalid_uuid_none(self):
        """Should return False for None."""
        from flip.utils import Utils

        assert Utils.is_valid_uuid(None) is False

    def test_invalid_uuid_wrong_format(self):
        """Should return False for wrong UUID format."""
        from flip.utils import Utils

        # Too short
        assert Utils.is_valid_uuid("550e8400-e29b") is False

        # Invalid characters
        assert Utils.is_valid_uuid("550e8400-ZZZZ-41d4-a716-446655440000") is False

    def test_invalid_uuid_integer(self):
        """Should handle integer input gracefully."""
        from flip.utils import Utils

        # Will convert to string "12345" which is not a UUID
        assert Utils.is_valid_uuid(12345) is False


class TestUtilsIsStringEmpty:
    """Test the is_string_empty utility function."""

    def test_empty_string(self):
        """Should return True for empty string."""
        from flip.utils import Utils

        assert Utils.is_string_empty("") is True

    def test_whitespace_only_string(self):
        """Should return True for whitespace-only string."""
        from flip.utils import Utils

        assert Utils.is_string_empty("   ") is True
        assert Utils.is_string_empty("\t") is True
        assert Utils.is_string_empty("\n") is True
        assert Utils.is_string_empty(" \t\n ") is True

    def test_non_empty_string(self):
        """Should return False for non-empty string."""
        from flip.utils import Utils

        assert Utils.is_string_empty("hello") is False
        assert Utils.is_string_empty(" hello ") is False

    def test_none_value_raises_error(self):
        """Should raise AttributeError for None value."""
        from flip.utils import Utils

        with pytest.raises(AttributeError):
            Utils.is_string_empty(None)
