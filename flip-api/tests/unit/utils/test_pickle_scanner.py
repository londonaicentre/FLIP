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

import pickle
import pickletools

import pytest

from flip_api.utils.pickle_scanner import is_pickle_bearing, scan_pickle_bytes


@pytest.mark.parametrize(
    ("ext", "expected"),
    [
        ("pt", True),
        ("pth", True),
        ("PKL", True),
        ("ckpt", True),
        ("py", False),
        ("json", False),
        ("", False),
    ],
)
def test_is_pickle_bearing(ext, expected):
    assert is_pickle_bearing(ext) is expected


def test_safe_pickle_passes():
    # Raw-pickle extension so picklescan parses as pickle directly rather than
    # trying to unwrap a torch-zip (which is what `.pt` triggers).
    safe_bytes = pickle.dumps({"weights": [0.1, 0.2, 0.3], "epoch": 5})
    result = scan_pickle_bytes(safe_bytes, "model.pkl")
    assert result.is_safe is True
    assert result.threats == ()


def test_malicious_pickle_with_os_system_is_flagged():
    """A hand-crafted pickle that calls os.system on load — the canonical
    pickle-RCE pattern that signature-based scanners cannot detect."""

    class Exploit:
        def __reduce__(self):
            import os

            return (os.system, ("echo pwned",))

    malicious_bytes = pickle.dumps(Exploit())
    # Sanity-check the payload actually contains a REDUCE opcode resolving to
    # os.system, otherwise the test would silently pass on a benign pickle.
    assert b"system" in pickletools.optimize(malicious_bytes)

    result = scan_pickle_bytes(malicious_bytes, "model.pkl")
    assert result.is_safe is False
    assert any("system" in threat for threat in result.threats)
