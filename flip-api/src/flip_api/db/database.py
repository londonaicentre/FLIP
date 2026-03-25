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

from collections.abc import Generator
from urllib.parse import quote_plus

from sqlmodel import Session, create_engine

from flip_api.config import get_settings
from flip_api.utils.get_secrets import get_secret

# Get database settings
stt = get_settings()

# Construct the database URL based on environment
if stt.ENV == "production":
    # In production, get password from AWS Secrets Manager
    db_password = get_secret(secret_key="password", secret_name=stt.POSTGRES_SECRET_ARN)

else:
    # In dev, password is provided via env variable (not secrets manager)
    db_password = stt.POSTGRES_PASSWORD

# URL-encode the password to handle special characters like @, #, %, etc.
# Remove leading/trailing whitespace in case there is a comment in the env file next to the password
encoded_password = quote_plus(db_password.strip())

db_url = f"postgresql+psycopg2://{stt.POSTGRES_USER}:{encoded_password}@{stt.DB_HOST}:{stt.DB_PORT}/{stt.POSTGRES_DB}"

# Create a synchronous engine
engine = create_engine(db_url, echo=False)


def get_session() -> Generator[Session, None, None]:
    """
    Create a new SQLModel session.

    Returns:
        Session: A new SQLModel session.
    """
    session = Session(engine)
    yield session
    session.close()
