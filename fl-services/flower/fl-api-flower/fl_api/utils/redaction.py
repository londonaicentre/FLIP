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

"""Best-effort secret masking for text that leaves the FL API.

Applied to Flower run logs before they are handed to the Central Hub (and from
there into ``fl_logs``, which the model owner reads in the UI). A run log is
whatever the ServerApp and its runtime wrote to stdout/stderr — researcher-
supplied code running in a container that holds ``INTERNAL_SERVICE_KEY`` and
AWS credentials — so it is not trusted to be secret-free. This is damage
limitation on a channel that should not carry secrets in the first place, not a
guarantee that none get through: a secret printed without a recognisable
keyword still passes.

Deliberately biased towards over-redaction. Masking a stray ``monkey=banana``
or ``learning_rate_key=0.1`` costs a reader nothing; leaking a service key
costs a rotation.
"""

import re

REDACTED = "[REDACTED]"

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # SigV4 query parameters on a presigned S3 URL. Each is a standalone
    # capability against the bucket for the life of the signature, so they are
    # masked individually rather than dropping the whole URL — the object path
    # is the useful half of the line.
    # No separator is required before the parameter name: whatever logged the URL
    # may have escaped `&` as `&amp;`, `\u0026` or `%26`, and a signature is a
    # signature however it was delimited.
    (
        re.compile(r"(?i)\b((?:X-Amz-Signature|X-Amz-Credential|X-Amz-Security-Token)=)[^&\s\"']+"),
        rf"\1{REDACTED}",
    ),
    # `Authorization: Bearer <jwt>` / `Basic <b64>` carry no keyword before the
    # separator, so the keyword rule below never sees them.
    (re.compile(r"(?i)\b((?:Bearer|Basic)\s+)[^\s\"',}]+"), rf"\1{REDACTED}"),
    # `<something>key|token|secret|password|credential|signature`, optionally
    # followed by one short suffix (`_BASE64`, `_HASH`, `_ID`), then `:` or `=`.
    # Covers header dumps (`X-Internal-Service-Key: ...`), env dumps
    # (`AWS_SECRET_ACCESS_KEY=...`, `AES_KEY_BASE64=...`), the SigV4 header form
    # (`Signature=...`) and kwargs repr in a traceback frame. Requiring the
    # separator right after the (suffixed) keyword keeps prose and `KeyError: 'x'`
    # out of it; stopping the value at `&` keeps it from eating the rest of a
    # query string the rule above has already masked.
    (
        re.compile(
            r"(?i)((?:[\w-]{0,64}(?:key|token|secret|password|passwd|credential|signature)"
            r"(?:[_-][a-z0-9]{1,12})?)[\"']?\s*[:=]\s*[\"']?)"
            r"[^\s\"',}&]+"
        ),
        rf"\1{REDACTED}",
    ),
    # A bare AWS access key id carries no keyword to key off, and is the one
    # credential shape distinctive enough to match on its own.
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
]


def redact_secrets(text: str) -> str:
    """Mask credential-shaped substrings in ``text``.

    Args:
        text (str): Arbitrary log text.

    Returns:
        str: The same text with recognised credentials replaced by ``[REDACTED]``.
    """
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
