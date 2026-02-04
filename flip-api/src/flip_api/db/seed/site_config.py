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

from sqlmodel import Session, select

from flip_api.db.models.main_models import SiteConfig


def seed_config(session: Session) -> None:
    """Seed site configuration."""
    deployment_mode = {"key": "DeploymentMode", "value": False}

    # Check if config exists
    statement = select(SiteConfig).where(SiteConfig.key == deployment_mode["key"])
    existing_config = session.exec(statement).first()

    if not existing_config:
        new_config = SiteConfig(**deployment_mode)
        session.add(new_config)
        session.commit()
