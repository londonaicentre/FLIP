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

from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

from flip_api.config import get_settings
from flip_api.utils.logger import logger


def parse_s3_path(s3_path: str) -> tuple[str, str]:
    """
    Parse an S3 path into bucket and key components.

    Args:
        s3_path: Full S3 path (e.g., s3://bucket-name/key)

    Returns:
        Tuple containing bucket name and key, e.g. ("bucket-name", "key")
    """
    parsed = urlparse(s3_path)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    return bucket, key


class S3Client:
    """S3 client wrapper for S3 operations."""

    def __init__(self):
        """Initialize S3 client with AWS credentials."""
        settings = get_settings()
        self.client = boto3.client("s3", region_name=settings.AWS_REGION)

    def get_presigned_url(self, s3_path: str, expiration: int = 3600) -> str:
        """
        Generate a pre-signed URL for downloading a file from S3.

        Args:
            s3_path: Full S3 path (e.g., s3://bucket-name/key)
            expiration: URL expiration time in seconds (default: 1 hour)

        Returns:
            str: Pre-signed URL string

        Raises:
            Exception: If URL generation fails
        """
        bucket, key = parse_s3_path(s3_path)

        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expiration,
        )
        return url

    def get_put_presigned_url(self, s3_path: str, expiration: int = 3600) -> str:
        """
        Generate a pre-signed URL for uploading a file to S3.

        Args:
            s3_path: Full S3 path (e.g., s3://bucket-name/key)
            expiration: URL expiration time in seconds (default: 1 hour)

        Returns:
            str: Pre-signed URL string

        Raises:
            Exception: If URL generation fails
        """
        try:
            bucket, key = parse_s3_path(s3_path)

            url = self.client.generate_presigned_url(
                "put_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expiration,
            )
            return url
        except ClientError as e:
            logger.error(f"Error generating pre-signed URL: {e}")
            raise Exception("Unable to create a pre-signed URL")

    def delete_object(self, s3_path: str) -> None:
        """
        Delete an object from S3 bucket.

        Args:
            s3_path: Full S3 path (e.g., s3://bucket-name/key)

        Raises:
            Exception: If deletion fails
        """
        try:
            bucket, key = parse_s3_path(s3_path)
            self.client.delete_object(Bucket=bucket, Key=key)
            logger.info(f"Deleted object {key} from bucket {bucket}")
        except ClientError as e:
            logger.error(f"Error deleting object {key} from bucket {bucket}: {e}")
            raise Exception(f"Unable to delete object {key} from bucket {bucket}")

    def delete_objects(self, s3_paths: list[str]) -> dict[str, Any]:
        """
        Delete multiple objects from one or more S3 buckets in grouped batch requests.

        Args:
            s3_paths: List of full S3 paths (e.g., s3://bucket-name/key)

        Returns:
            dict[str, Any]: Dictionary containing deletion results per bucket.

        Raises:
            Exception: If batch deletion fails for any bucket
        """
        try:
            # Group objects by bucket
            bucket_objects = defaultdict(list)
            for s3_path in s3_paths:
                bucket, key = parse_s3_path(s3_path)
                if not bucket:
                    logger.error(f"Invalid S3 path: {s3_path}")
                    continue
                bucket_objects[bucket].append({"Key": key})

            all_responses = {}
            for bucket, objects in bucket_objects.items():
                try:
                    response = self.client.delete_objects(
                        Bucket=bucket,
                        Delete={
                            "Objects": objects,
                            "Quiet": False,
                        },
                    )
                    deleted = [obj["Key"] for obj in response.get("Deleted", [])]
                    errors = [f"{err['Key']} - {err['Code']}: {err['Message']}" for err in response.get("Errors", [])]

                    if errors:
                        logger.warning(f"Partial success deleting objects from bucket {bucket}. Errors: {errors}")
                    else:
                        logger.info(f"Successfully deleted {len(deleted)} objects from bucket {bucket}")

                    all_responses[bucket] = response

                except ClientError as e:
                    logger.error(f"Error batch deleting objects from bucket {bucket}: {e}")
                    raise Exception(f"Unable to delete objects from bucket {bucket}: {str(e)}")

            return all_responses

        except Exception:
            logger.exception("Failed to delete S3 objects.")
            raise

    def get_object(self, s3_path: str) -> dict[str, Any]:
        """
        Get object from S3 bucket.

        Args:
            s3_path: Full S3 path (e.g., s3://bucket-name/key)

        Returns:
            dict[str, Any]: Response containing object data.

        Raises:
            EndpointConnectionError: If connection to the S3 endpoint fails.
        """
        bucket, key = parse_s3_path(s3_path)
        try:
            response = self.client.get_object(Bucket=bucket, Key=key)
            return response
        except ClientError as e:
            logger.error(f"Error getting object {key} from bucket {bucket}: {e}")
            raise EndpointConnectionError(
                endpoint_url=f"https://{bucket}.s3.your-region.amazonaws.com/{key}",
                error=e,
            )

    def head_object(self, s3_path: str) -> dict[str, Any]:
        """
        Get object metadata from S3.

        Args:
            s3_path: Full S3 path (e.g., s3://bucket-name/key)

        Returns:
            dict[str, Any]: Metadata of the object.

        Raises:
            Exception: If getting object metadata fails.
        """
        bucket, key = parse_s3_path(s3_path)
        try:
            response = self.client.head_object(Bucket=bucket, Key=key)
            return response
        except ClientError as e:
            logger.error(f"Error getting object metadata for {key} from bucket {bucket}: {e}")
            raise Exception(f"Unable to get object metadata for {key} from bucket {bucket}")

    def object_exists(self, s3_path: str) -> bool:
        """
        Check if an object exists in S3.

        Args:
            s3_path: Full S3 path (e.g., s3://bucket-name/key)

        Returns:
            bool: True if the object exists, False otherwise.

        Raises:
            ClientError: If ``head_object`` fails with any error other than HTTP 404 (e.g.,
                permissions issues).
        """
        bucket, key = parse_s3_path(s3_path)
        try:
            self.client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            logger.error(f"Error checking if object {key} exists in bucket {bucket}: {e}")
            raise

    def copy_object(self, source_s3_path: str, dest_s3_path: str) -> None:
        """
        Copy object from source to destination bucket.

        Args:
            source_s3_path: Full S3 path of the source object (e.g., s3://source-bucket/key)
            dest_s3_path: Full S3 path of the destination object (e.g., s3://dest-bucket/key)

        Raises:
            Exception: If copying the object fails.
        """
        try:
            source_bucket, source_key = parse_s3_path(source_s3_path)
            dest_bucket, dest_key = parse_s3_path(dest_s3_path)

            copy_source = {"Bucket": source_bucket, "Key": source_key}
            self.client.copy_object(CopySource=copy_source, Bucket=dest_bucket, Key=dest_key)
            logger.info(f"Successfully copied {source_s3_path} to {dest_s3_path}")
        except ClientError as e:
            logger.error(f"Error copying {source_s3_path} to {dest_s3_path}: {e}")
            raise Exception(f"Unable to copy object: {e}")

    def list_objects(self, s3_path: str, delimiter: str = "") -> list[str]:
        """
        List object keys under a given S3 path (non-paginated) and return full S3 paths.

        Args:
            s3_path: Full S3 path (e.g., s3://bucket-name/prefix/)
            delimiter: Character used to group keys (optional)

        Returns:
            List of full S3 paths (e.g., s3://bucket/key)

        Raises:
            HTTPException: If listing objects fails
        """
        try:
            bucket, prefix = parse_s3_path(s3_path)
            if not bucket:
                raise ValueError(f"Invalid S3 path: {s3_path}")

            params = {
                "Bucket": bucket,
                "Prefix": prefix,
            }
            if delimiter:
                params["Delimiter"] = delimiter

            response = self.client.list_objects_v2(**params)
            contents = response.get("Contents", [])

            # Build full s3 paths from client response
            # Filter out directories (keys ending with '/')
            full_s3_paths = [f"s3://{bucket}/{obj['Key']}" for obj in contents if not obj["Key"].endswith("/")]

            return full_s3_paths

        except ClientError as e:
            error_message = f"Error listing objects under '{s3_path}': {e}"
            logger.error(error_message, exc_info=True)
            raise Exception(error_message)
        except ValueError as ve:
            logger.error(str(ve))
            raise Exception(str(ve))
