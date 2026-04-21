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

import hashlib
from unittest.mock import patch

from flip_api.scripts.generate_trust_key import generate_trust_key


class TestGenerateTrustKey:
    def test_returns_key_and_hash(self):
        """generate_trust_key should return a non-empty key and its SHA-256 hash."""
        key, key_hash = generate_trust_key()

        assert isinstance(key, str)
        assert len(key) > 0
        assert isinstance(key_hash, str)
        assert len(key_hash) == 64  # SHA-256 hex digest length

    def test_hash_matches_sha256_of_key(self):
        """The returned hash must equal hashlib.sha256(key.encode()).hexdigest()."""
        key, key_hash = generate_trust_key()

        expected_hash = hashlib.sha256(key.encode()).hexdigest()
        assert key_hash == expected_hash

    def test_generates_unique_keys(self):
        """Each call should produce a different key."""
        key1, _ = generate_trust_key()
        key2, _ = generate_trust_key()
        assert key1 != key2


class TestGenerateTrustKeyMain:
    def test_main_prints_key_info(self, capsys):
        """main() should print the trust name, key, and hash."""
        with (
            patch("sys.argv", ["generate_trust_key", "--trust-name", "Trust_CLI"]),
            patch(
                "flip_api.scripts.generate_trust_key.generate_trust_key",
                return_value=("test-key-abc", "test-hash-def"),
            ) as mock_gen,
        ):
            from flip_api.scripts.generate_trust_key import main

            main()

        mock_gen.assert_called_once_with()
        captured = capsys.readouterr()
        assert "Trust_CLI" in captured.out
        assert "test-key-abc" in captured.out
        assert "test-hash-def" in captured.out
