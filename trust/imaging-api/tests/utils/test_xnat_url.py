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

from urllib.parse import urlsplit

import pytest
import requests

from imaging_api.utils.xnat_url import xnat_url


def test_xnat_url_encodes_raw_path_and_query_separately():
    request = requests.Request(
        "GET", xnat_url("data", "image ?#% α/&", query={"format": "json", "name": "image ?#% α/&"})
    ).prepare()
    parsed = urlsplit(request.url)
    assert parsed.path == "/data/image%20%3F%23%25%20%CE%B1%2F%26"
    assert parsed.query == "format=json&name=image+%3F%23%25+%CE%B1%2F%26"
    assert parsed.fragment == ""


@pytest.mark.parametrize("segment", ["", ".", ".."])
def test_xnat_url_rejects_unusable_segments(segment):
    with pytest.raises(ValueError, match="empty or a dot-segment"):
        xnat_url("data", segment)
