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

from uuid import UUID

from sqlmodel import Session

from flip_api.db.models.main_models import FLMetrics
from flip_api.domain.schemas.private import TrainingMetrics
from flip_api.utils.logger import logger


def save_training_metrics(model_id: UUID, training_metrics: TrainingMetrics, db: Session) -> None:
    """
    Saves the provided training metrics to the database.

    Args:
        model_id (UUID): The ID of the model these metrics belong to.
        training_metrics (TrainingMetrics): The metrics payload reported by a trust.
        db (Session): The SQLModel session used for the insert.

    Raises:
        Exception: Re-raises any database error after rolling back the session.
    """
    logger.info(f"Attempting to save training metrics for model_id: {model_id}, trust: {training_metrics.trust}")

    # Create a new FLMetrics instance
    metrics = FLMetrics(
        trust=training_metrics.trust,
        model_id=model_id,
        global_round=training_metrics.global_round,
        label=training_metrics.label,
        result=training_metrics.result,
    )

    # Add the metrics to the database session
    try:
        db.add(metrics)
        db.commit()
        db.refresh(metrics)  # Optional: if you need the ID or other db-generated fields
        logger.info(f"Successfully saved training metrics for model_id: {model_id}, trust: {training_metrics.trust}")

    except Exception as e:
        db.rollback()
        logger.error(
            f"Database error while saving training metrics for model {model_id}, trust {training_metrics.trust}: {e}",
            exc_info=True,
        )
        raise  # Re-raise to be caught by the endpoint's general error handler
