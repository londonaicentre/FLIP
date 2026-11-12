#  Copyright 2026 London AI Centre
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""The ``/api/*`` origin-request policy forwards a fixed list of headers, and no job credential.

The FL server's callbacks to flip-api are authorised by ``X-Job-Credential`` alone and travel inside
the hub's network (``FLIP_API_INTERNAL_URL``). A policy that forwarded that header, or every header,
would let the same callbacks be attempted from the internet.
"""

import re
from pathlib import Path

from tf_source import hcl_block, strip_comments

CLOUDFRONT_TF = Path(__file__).resolve().parent.parent / "cloudfront.tf"
FORWARDED_HEADERS = {"Authorization", "Content-Type", "Origin"}


def _headers_config() -> str:
    policy = hcl_block(
        strip_comments(CLOUDFRONT_TF.read_text(encoding="utf-8")),
        'resource "aws_cloudfront_origin_request_policy" "flip_api"',
    )
    return hcl_block(policy, "headers_config")


def test_headers_are_forwarded_from_a_whitelist() -> None:
    assert re.search(r'header_behavior\s*=\s*"whitelist"', _headers_config())


def test_only_the_documented_headers_are_forwarded() -> None:
    items = re.search(r"items\s*=\s*\[([^\]]*)\]", _headers_config())
    assert items, "the policy's headers block lists no items"
    forwarded = set(re.findall(r'"([^"]+)"', items[1]))

    assert forwarded == FORWARDED_HEADERS
    assert "x-job-credential" not in {header.lower() for header in forwarded}
