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

from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
import requests


@pytest.fixture
def headers():
    return {}


@pytest.fixture
def sent_xnat_requests():
    """Prepare real Requests URLs while replacing only the network dispatch."""
    sent = []

    def respond(request, **kwargs):
        sent.append(request)
        response = requests.Response()
        response.status_code = 404 if request.method == "GET" and urlsplit(request.url).query == "inbody=true" else 200
        response._content = b"{}"
        response._content_consumed = True
        response.request = request
        return response

    with patch.object(requests.Session, "send", side_effect=respond):
        yield sent


@pytest.fixture(
    params=[
        ("ACC-123._~", "ACC-123._~"),
        ("../other", "..%2Fother"),
        ("label?format=xml&other=yes", "label%3Fformat%3Dxml%26other%3Dyes"),
        ("label#fragment", "label%23fragment"),
        ("%2e%2e%2fadmin", "%252e%252e%252fadmin"),
        ("path\\other", "path%5Cother"),
        ("image data", "image%20data"),
        ("αβ", "%CE%B1%CE%B2"),
    ]
)
def xnat_path_segment(request):
    """Raw identifiers and literal expected encodings, independent of the URL helper."""
    return request.param
