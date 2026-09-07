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

import pytest
from pydantic import ValidationError

from flip_api.domain.interfaces.site import ISiteBanner


class TestISiteBannerLinkValidation:
    """The banner link is rendered into an href every user sees, so the scheme is allow-listed."""

    @pytest.mark.parametrize(
        "link",
        [
            "javascript:alert(1)",
            "JaVaScRiPt:alert(1)",
            "\tjavascript:alert(1)",
            " javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "ftp://example.com/x",
            "/relative/path",
        ],
    )
    def test_rejects_non_http_schemes(self, link: str):
        with pytest.raises(ValidationError):
            ISiteBanner.model_validate({"message": "m", "link": link, "enabled": True})

    @pytest.mark.parametrize("link", ["https://example.nhs.uk/guidance", "http://example.nhs.uk/guidance"])
    def test_accepts_http_schemes(self, link: str):
        banner = ISiteBanner.model_validate({"message": "m", "link": link, "enabled": True})

        assert str(banner.link) == link

    @pytest.mark.parametrize("link", ["", "   ", None])
    def test_treats_blank_as_no_link(self, link: str | None):
        """update_site_details persists "" rather than NULL, so blank must validate as absent.

        Without this every existing link-less banner would fail validation on read.
        """
        banner = ISiteBanner.model_validate({"message": "m", "link": link, "enabled": True})

        assert banner.link is None
