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

from typing import Annotated

from pydantic import BaseModel, Field, constr

# Schemas

trim_str = Annotated[str, constr(min_length=1, strip_whitespace=True)]


class UpdateTrustStatusSchema(BaseModel):
    fl_client_endpoint: trim_str = Field(..., description="'fl_client_endpoint' is required")
