# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Lambda function to stop and start the FLIP RDS instance on a schedule."""

import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    """Stop or start the RDS instance based on the event action."""
    rds = boto3.client("rds")
    db_identifier = os.environ["DB_IDENTIFIER"]
    action = event.get("action")

    if action == "stop":
        try:
            rds.stop_db_instance(DBInstanceIdentifier=db_identifier)
            logger.info("Stopping RDS instance: %s", db_identifier)
        except rds.exceptions.InvalidDBInstanceStateFault:
            logger.info("Instance %s cannot be stopped in its current state", db_identifier)
    elif action == "start":
        try:
            rds.start_db_instance(DBInstanceIdentifier=db_identifier)
            logger.info("Starting RDS instance: %s", db_identifier)
        except rds.exceptions.InvalidDBInstanceStateFault:
            logger.info("Instance %s cannot be started in its current state", db_identifier)
    else:
        logger.error("Unknown action: %s", action)
        return {"statusCode": 400, "body": f"Unknown action: {action}"}

    return {"statusCode": 200, "body": f"{action} completed for {db_identifier}"}
