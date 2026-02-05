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

from fastapi import HTTPException, status
from sqlmodel import Session, select

from flip_api.db.models.main_models import SiteBanner, SiteConfig
from flip_api.domain.interfaces.site import ISiteBanner, ISiteDetails
from flip_api.utils.logger import logger


def get_site_details(db: Session) -> ISiteDetails:
    """
    Fetch site details from the database.

    Args:
        db (Session): Database session.

    Returns:
        SiteDetails: Current site details including banner and deployment mode.
    """
    # Always get the first banner. Ideally there should only be one banner.
    banner = db.get(SiteBanner, 1)

    # Get the deployment mode config
    config = db.exec(select(SiteConfig).where(SiteConfig.key == "DeploymentMode")).first()

    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment mode not found")

    if banner:
        validated_banner = ISiteBanner.model_validate(banner.model_dump())

        return ISiteDetails(
            banner=ISiteBanner(
                message=validated_banner.message,
                link=validated_banner.link if banner.link and banner.link.strip() != "" else None,
                enabled=validated_banner.enabled,
            ),
            deploymentMode=config.value,
        )
    else:
        return ISiteDetails(
            banner=ISiteBanner(
                message="This is a default banner message.",
                link=None,
                enabled=False,
            ),
            deploymentMode=config.value,
        )


def update_site_details(site_details: ISiteDetails, db: Session) -> None:
    """
    Update site details in the database.

    Args:
        site_details (ISiteDetails): Updated site configuration.
        db (Session): Database session.

    Returns:
        None
    """
    try:
        # Update or insert the banner
        banner = db.get(SiteBanner, 1)
        logger.info(f"Current banner: {banner}")

        if site_details.banner:
            if banner:
                banner.message = site_details.banner.message
                banner.link = str(site_details.banner.link) if site_details.banner.link else ""
                banner.enabled = site_details.banner.enabled
            else:
                banner = SiteBanner(
                    message=site_details.banner.message,
                    link=str(site_details.banner.link) if site_details.banner.link else "",
                    enabled=site_details.banner.enabled,
                )
                db.add(banner)
            db.commit()

        # Update or insert deployment mode
        config = db.exec(select(SiteConfig).where(SiteConfig.key == "DeploymentMode")).first()
        logger.info(f"Current deployment mode config: {config}")

        if config:
            config.value = site_details.deploymentMode
        else:
            config = SiteConfig(key="DeploymentMode", value=site_details.deploymentMode)
            db.add(config)

        db.commit()

        logger.info({
            "message": "We updated the site details...",
            "bannerUpdated": bool(site_details.banner),
            "deploymentMode": site_details.deploymentMode,
        })

        return

    except Exception as e:
        db.rollback()
        logger.exception(f"Error updating site details: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error updating site details: {str(e)}"
        )
