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

import string

from imaging_api.utils.passwords import generate_complex_password

SPECIAL_CHARS = "!@#$%^&*()-_=+[]{}|;:,.<>?"


def test_default_length():
    password = generate_complex_password()
    assert len(password) == 15


def test_custom_length():
    password = generate_complex_password(length=20)
    assert len(password) == 20


def test_contains_all_character_sets():
    password = generate_complex_password(length=50)
    assert any(c in string.ascii_lowercase for c in password)
    assert any(c in string.ascii_uppercase for c in password)
    assert any(c in string.digits for c in password)
    assert any(c in SPECIAL_CHARS for c in password)


def test_uniqueness():
    passwords = {generate_complex_password() for _ in range(10)}
    assert len(passwords) == 10
