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

"""Unit tests for the pure CORS-origin normalisers in ``flip_api.utils.cors``.

Fetching the allowlist from the identity backend is the provider's job
(``IdentityProvider.allowed_origins``); those tests live with the provider in
``tests/unit/auth/identity/``. This module covers only the URL → origin arithmetic.
"""

import pytest

from flip_api.utils.cors import _origin_from_url, normalise_origins


class TestOriginFromUrl:
    """Tests for the _origin_from_url normalizer used by the CORS allowlist builder."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            # Default ports must be stripped — browsers omit them from the Origin header.
            ("https://localhost:443", "https://localhost"),
            ("http://example.com:80/path", "http://example.com"),
            # Non-default ports must be preserved.
            ("http://localhost:8080", "http://localhost:8080"),
            ("https://localhost:8443/", "https://localhost:8443"),
            # Path / query / fragment are dropped.
            ("https://app.flip.aicentre.co.uk/login?x=1#frag", "https://app.flip.aicentre.co.uk"),
            # Hostname is lowercased by urlparse.
            ("https://APP.FLIP.aicentre.co.uk", "https://app.flip.aicentre.co.uk"),
        ],
    )
    def test_normalizes_origin(self, url, expected):
        assert _origin_from_url(url) == expected

    @pytest.mark.parametrize("url", ["", "not-a-url", "/just/a/path"])
    def test_returns_none_for_unusable_input(self, url):
        assert _origin_from_url(url) is None


class TestNormaliseOrigins:
    """Tests for normalise_origins: the list form every identity backend feeds ``CORSMiddleware``."""

    def test_normalizes_dedupes_and_preserves_order(self):
        urls = [
            "https://app.flip.aicentre.co.uk",
            "https://localhost:443",
            # Duplicate after normalization (path dropped) — must be deduped, first position kept.
            "https://app.flip.aicentre.co.uk/callback",
            "http://localhost:8080",
            # Duplicate after normalization (default port stripped).
            "https://localhost",
        ]

        assert normalise_origins(urls) == [
            "https://app.flip.aicentre.co.uk",
            "https://localhost",
            "http://localhost:8080",
        ]

    def test_drops_unusable_entries(self):
        assert normalise_origins(["", "not-a-url", "/just/a/path", "https://ok.example.com"]) == [
            "https://ok.example.com"
        ]

    def test_empty_input_gives_empty_list(self):
        assert normalise_origins([]) == []
