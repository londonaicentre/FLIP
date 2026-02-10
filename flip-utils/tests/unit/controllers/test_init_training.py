# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from unittest.mock import MagicMock

import pytest

from flip.constants import FlipConstants
from flip.controllers.init_training import InitTraining


class TestInitTraining:
    def test_init_with_valid_uuid(self):
        """Test initialization with valid UUID"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitTraining(model_id=model_id)
        assert controller._model_id == model_id
        assert controller._min_clients == FlipConstants.MIN_CLIENTS

    def test_init_with_invalid_uuid_raises_error(self):
        """Test initialization with invalid UUID raises ValueError"""
        mock_flip = MagicMock()
        with pytest.raises(ValueError, match="not a valid UUID"):
            InitTraining(model_id="invalid-uuid", flip=mock_flip)

    def test_init_with_min_clients(self):
        """Test initialization with custom min_clients"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitTraining(model_id=model_id, min_clients=3)
        assert controller._min_clients == 3

    def test_init_with_invalid_min_clients_raises_error(self):
        """Test initialization with invalid min_clients raises ValueError"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        mock_flip = MagicMock()
        with pytest.raises(ValueError, match="Invalid number of minimum clients"):
            InitTraining(model_id=model_id, min_clients=0, flip=mock_flip)

    def test_init_with_negative_cleanup_timeout_raises_error(self):
        """Test initialization with negative cleanup_timeout raises ValueError"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        mock_flip = MagicMock()
        with pytest.raises(ValueError, match="cleanup_timeout must be greater"):
            InitTraining(model_id=model_id, cleanup_timeout=-1, flip=mock_flip)

    def test_init_with_custom_cleanup_timeout(self):
        """Test initialization with custom cleanup_timeout"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        controller = InitTraining(model_id=model_id, cleanup_timeout=300)
        assert controller._cleanup_timeout == 300
