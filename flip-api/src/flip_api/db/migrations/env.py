# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Alembic environment for flip-api.

Reuses flip-api's own SQLAlchemy machinery so migrations inherit the exact same
database URL + secret handling as the application:

* ``target_metadata`` is ``SQLModel.metadata`` — populated by importing the two
  model modules below (the imports look unused but are load-bearing).
* Online migrations prefer a live ``Connection`` injected programmatically via
  ``config.attributes["connection"]`` (the integration suite points this at its
  Testcontainers Postgres). When none is supplied — the CLI / entrypoint path —
  we fall back to ``flip_api.db.database.engine``, which already encapsulates the
  prod AWS Secrets Manager password lookup and the dev env-var URL.
"""

from logging.config import fileConfig
from typing import Any, Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy.engine import Connection
from sqlmodel import SQLModel

# Importing the models registers their tables on ``SQLModel.metadata``. Without
# these imports autogenerate would see an empty metadata and emit a DROP for
# every existing table — do not remove them.
import flip_api.db.models.main_models  # noqa: F401
import flip_api.db.models.user_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def render_item(type_: str, obj: Any, autogen_context: AutogenContext) -> str | Literal[False]:
    """Render SQLModel's ``AutoString`` as a plain SQLAlchemy ``String`` in migrations.

    SQLModel maps ``str`` fields to ``sqlmodel.sql.sqltypes.AutoString`` (a thin
    VARCHAR wrapper). Rendering it verbatim would force every generated migration
    to ``import sqlmodel``; emitting ``sa.String(...)`` instead keeps migration
    files dependency-light and produces identical DDL.

    Args:
        type_ (str): The kind of object Alembic is rendering ("type", "server_default", …).
        obj (Any): The object to render (a SQLAlchemy type instance when ``type_`` is "type").
        autogen_context (AutogenContext): Alembic's autogenerate context (unused).

    Returns:
        str | bool: The replacement render string for SQLModel types, else ``False`` to
            let Alembic fall back to its default rendering.
    """
    if type_ == "type" and obj.__class__.__module__.startswith("sqlmodel"):
        length = getattr(obj, "length", None)
        return f"sa.String(length={length})" if length is not None else "sa.String()"
    return False


def _configure_and_run(connection: Connection) -> None:
    """Configure the migration context against a connection and run it.

    Args:
        connection (Connection): An open SQLAlchemy connection to migrate.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live DB connection).

    Unused by the entrypoint and the test suite — both run online — but kept so
    ``alembic upgrade --sql`` still works. The URL is taken from flip-api's engine
    so there is still a single source of truth for the connection string.
    """
    from flip_api.db.database import engine

    context.configure(
        url=str(engine.url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live connection."""
    injected = config.attributes.get("connection", None)
    if injected is not None:
        _configure_and_run(injected)
        return

    # Lazy import so injecting a connection never triggers building the prod
    # engine (which performs the AWS Secrets Manager lookup at import time).
    from flip_api.db.database import engine

    with engine.connect() as connection:
        _configure_and_run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
