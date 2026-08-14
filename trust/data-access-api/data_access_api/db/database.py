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

from sqlalchemy import create_engine

from data_access_api.config import get_settings

engine = create_engine(
    get_settings().OMOP_DATABASE_URL.get_secret_value(),
    echo=False,
    # Keep bind-parameter values (e.g. person_id lists from the statistics
    # queries) out of SQLAlchemy error text, which otherwise renders them in a
    # ``[parameters: ...]`` suffix on every wrapped driver error (logging policy).
    hide_parameters=True,
)
