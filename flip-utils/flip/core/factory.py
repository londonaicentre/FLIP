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

"""
FLIP Factory.

This module provides the FLIP() factory function that returns the appropriate
FLIP implementation based on job type and environment.
"""

from typing import Union

from flip.constants.flip_constants import FlipConstants
from flip.constants.job_types import JobType, JobTypeStr
from flip.core.base import FLIPBase


def FLIP(job_type: Union[JobType, JobTypeStr] = JobType.STANDARD, **kwargs) -> FLIPBase:
    """
    Factory function to create appropriate FLIP instance based on job type.

    This is the main entry point for users to create FLIP instances.
    The factory automatically selects the correct implementation based on:
    1. The job type (standard, evaluation, fed_opt, diffusion_model)
    2. The environment (LOCAL_DEV or production)

    Args:
        job_type: One of "standard", "evaluation", "fed_opt", "diffusion_model"
                  or a JobType enum value. Defaults to "standard".
        **kwargs: Additional arguments passed to the constructor

    Returns:
        FLIPBase: Appropriate FLIP instance for the job type and environment

    Example:
        # Create standard FLIP instance
        flip = FLIP()
        df = flip.get_dataframe(project_id, query)

        # Create evaluation-specific FLIP instance
        flip = FLIP(job_type="evaluation")

        # Using enum
        from flip.constants import JobType
        flip = FLIP(job_type=JobType.DIFFUSION)
    """
    is_dev = FlipConstants.LOCAL_DEV

    # Normalize job_type string to enum
    if isinstance(job_type, str):
        try:
            job_type = JobType(job_type)
        except ValueError:
            valid_types = [t.value for t in JobType]
            raise ValueError(f"Unknown job_type: {job_type}. Must be one of {valid_types}")

    # Map job types to their implementations
    if job_type in (JobType.STANDARD, JobType.EVALUATION, JobType.FED_OPT):
        return _create_standard_flip(is_dev, **kwargs)
    elif job_type == JobType.DIFFUSION:
        return _create_diffusion_flip(is_dev, **kwargs)
    else:
        valid_types = [t.value for t in JobType]
        raise ValueError(f"Unknown job_type: {job_type}. Must be one of {valid_types}")


def _create_standard_flip(is_dev: bool, **kwargs) -> FLIPBase:
    """Create a standard FLIP instance based on environment."""
    from flip.core.standard import FLIPStandardDev, FLIPStandardProd

    return FLIPStandardDev(**kwargs) if is_dev else FLIPStandardProd(**kwargs)


def _create_diffusion_flip(is_dev: bool, **kwargs) -> FLIPBase:
    """Create a diffusion model FLIP instance based on environment."""
    # For now, use the standard implementation.
    # The diffusion model has slightly different send_metrics_value signature
    # (no round parameter), but we can handle this at the executor level.
    # If needed, a dedicated FLIPDiffusion class can be created later.
    from flip.core.standard import FLIPStandardDev, FLIPStandardProd

    return FLIPStandardDev(**kwargs) if is_dev else FLIPStandardProd(**kwargs)
