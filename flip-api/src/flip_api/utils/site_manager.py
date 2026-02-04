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

from sqlmodel import Session, col, select

from flip_api.db.models.main_models import SiteConfig
from flip.utils.logger import logger


def is_deployment_mode_enabled(db: Session) -> bool:
    """
    Check whether deployment mode is enabled by querying site_config table.

    Args:
        db: SQLModel database session

    Returns:
        bool: True if deployment mode is enabled, False otherwise
    """
    logger.debug("Attempting to check whether deployment mode is enabled...")

    statement = select(SiteConfig).where(SiteConfig.key == "DeploymentMode", col(SiteConfig.value).is_(True))
    result = db.exec(statement).first()

    is_enabled = result is not None

    logger.debug(f"Deployment mode enabled: {is_enabled}")

    return is_enabled
