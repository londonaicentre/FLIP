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

# Custom exceptions
class NotFoundError(Exception):
    """Exception raised when a resource is not found."""

    def __init__(self, detail: str = "Resource not found", status_code: int = 404):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")


class AlreadyExistsError(Exception):
    """Exception raised when a resource already exists."""

    pass


class InternalServerError(Exception):
    """Exception raised for internal server errors."""

    def __init__(self, detail: str = "Internal server error", status_code: int = 500):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")
