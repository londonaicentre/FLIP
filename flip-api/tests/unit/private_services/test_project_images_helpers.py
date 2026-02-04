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

import uuid
from unittest.mock import MagicMock

from flip_api.db.models.main_models import XNATImageStatus, XNATProjectStatus
from flip.private_services.project_images_helpers import (
    insert_status,
    update_status,
)

# Common test data
trust_id = uuid.uuid4()
xnat_project_id = uuid.uuid4()
project_id = uuid.uuid4()
status = XNATImageStatus.RETRIEVE_IN_PROGRESS
query_id = uuid.uuid4()


class TestUpdateStatus:
    def test_update_status_success(self, mock_db_session: MagicMock):
        # Arrange
        new_status = XNATImageStatus.RETRIEVE_COMPLETED

        mock_db_session.exec.return_value.first.return_value = XNATProjectStatus(
            xnat_project_id=xnat_project_id,
            project_id=project_id,
            trust_id=trust_id,
            retrieve_image_status=status,
        )

        # Act
        returned_rowcount = update_status(trust_id, xnat_project_id, project_id, new_status, mock_db_session)

        # Assert
        assert returned_rowcount == 1
        mock_db_session.commit.assert_called_once()


class TestInsertStatus:
    def test_insert_status_success_with_query_id(self, mock_db_session: MagicMock):
        # Act
        returned_rowcount = insert_status(trust_id, xnat_project_id, project_id, status, mock_db_session, query_id)

        # Assert
        assert returned_rowcount == 1
        mock_db_session.commit.assert_called_once()

    def test_insert_status_success_without_query_id(self, mock_db_session: MagicMock):
        # Act
        returned_rowcount = insert_status(
            trust_id, xnat_project_id, project_id, status, mock_db_session
        )  # query_id is None by default

        # Assert
        assert returned_rowcount == 1
        mock_db_session.commit.assert_called_once()
