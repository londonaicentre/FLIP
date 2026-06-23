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

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase


# Tell mypy explicitly that Base is a type, by giving it a type annotation:
class Base(DeclarativeBase):
    """Base class for SQLAlchemy ORM models."""

    pass


class DirectArchiveSessionORM(Base):
    """ORM model for the xhbm_direct_archive_session table."""

    __tablename__ = "xhbm_direct_archive_session"

    id = Column(Integer, primary_key=True)
    created = Column(DateTime)
    folder_name = Column(String)
    status = Column(String)
    project = Column(String)
    timestamp = Column(DateTime)


class DirectArchiveSession(BaseModel):
    """Pydantic model for the xhbm_direct_archive_session table."""

    id: int
    created: datetime
    folder_name: str
    status: str
    project: str

    model_config = ConfigDict(from_attributes=True)


class ExecutedPacsRequestORM(Base):
    """ORM model for the xhbm_executed_pacs_request table."""

    __tablename__ = "xhbm_executed_pacs_request"

    id = Column(Integer, primary_key=True)
    created = Column(DateTime)
    accession_number = Column(String)
    status = Column(String)
    xnat_project = Column(String)
    timestamp = Column(DateTime)


class ExecutedPacsRequest(BaseModel):
    """Pydantic model for the xhbm_executed_pacs_request table."""

    id: int
    created: datetime
    accession_number: str
    status: str
    xnat_project: str

    model_config = ConfigDict(from_attributes=True)


class QueuedPacsRequestORM(Base):
    """ORM model for the xhbm_queued_pacs_request table."""

    __tablename__ = "xhbm_queued_pacs_request"

    id = Column(Integer, primary_key=True)
    created = Column(DateTime)
    accession_number = Column(String)
    status = Column(String)
    xnat_project = Column(String)
    timestamp = Column(DateTime)


class QueuedPacsRequest(BaseModel):
    """Pydantic model for the xhbm_queued_pacs_request table."""

    id: int
    created: datetime
    accession_number: str
    status: str
    xnat_project: str

    model_config = ConfigDict(from_attributes=True)
