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

"""Endpoint for retrieving available job types and their required files (moved to model_services)."""

from typing import Dict, List

from fastapi import APIRouter

from flip_api.domain.interfaces.fl import JobRequiredFiles
from flip.utils.logger import logger

router = APIRouter(prefix="/model", tags=["model_services"])


@router.get("/job-types", response_model=Dict[str, List[str]])
def get_job_types_endpoint() -> Dict[str, List[str]]:
    """Retrieve all available job types and their required files.

    This endpoint returns a dictionary mapping each job type name to its
    list of required files. This allows the UI to dynamically determine
    which files are required for training based on the config.json job_type.

    Returns:
        Dict[str, List[str]]: A dictionary where keys are job type names
            (e.g., 'standard', 'evaluation') and values are lists of required
            file names (e.g., ['trainer.py', 'validator.py', 'config.json']).

    Example Response:
        {
            "standard": ["trainer.py", "validator.py", "models.py", "config.json"],
            "evaluation": ["config.json", "evaluator.py"],
            "fed_opt": ["trainer.py", "validator.py", "config.json"],
            "diffusion_model": ["trainer.py", "validator.py", "config.json", "models.py"]
        }
    """

    logger.info("[API] /model/job-types endpoint called")
    job_types = JobRequiredFiles.get_all_job_types_with_files()
    logger.info(f"[API] /model/job-types response: {job_types}")
    return job_types
