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

import pytest


@pytest.fixture
def model_id():
    """Valid UUID model ID fixture."""
    return "b804316b-8181-40ff-af44-13e62cebe9dd"


@pytest.fixture
def create_model_data(session, model_factory, create_project_data, model_id):
    """Fixture for creating model data."""
    try:
        project = create_project_data
        model = model_factory.build(project_id=project.id, id=model_id)
        session.add(model)
        session.flush()  # Ensure the model is added to the session
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"Error creating project data: {e}")
    yield model
    try:
        session.refresh(model)
        session.delete(model)
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"Error creating project data: {e}")
