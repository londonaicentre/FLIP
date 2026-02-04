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

import secrets
import string


def generate_complex_password(length: int = 15) -> str:
    """
    Generate a complex password similar to MlkPwgen.PasswordGenerator.GenerateComplex.

    Args:
        length (int): Length of the password to generate. Default is 15.
    """
    # Define character sets
    lowercase = string.ascii_lowercase
    uppercase = string.ascii_uppercase
    digits = string.digits
    special_chars = "!@#$%^&*()-_=+[]{}|;:,.<>?"

    # Ensure at least one from each character set
    password = [
        secrets.choice(lowercase),
        secrets.choice(uppercase),
        secrets.choice(digits),
        secrets.choice(special_chars),
    ]

    # Fill the rest of the password length with random chars from all sets
    all_chars = lowercase + uppercase + digits + special_chars
    password.extend(secrets.choice(all_chars) for _ in range(length - 4))

    # Shuffle the password characters
    secrets.SystemRandom().shuffle(password)

    return "".join(password)
