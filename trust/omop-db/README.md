<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

# Trust OMOP database

Postgres database containing OMOP-ified data.

## Set up

We have prepared mock data for each of the 2 dev trusts (Trust_1 and Trust_2) as postgres data volumes on S3. In order to set up the database locally, these data volumes need to be downloaded/extracted. This will be handled automatically when
creating the trust containers, and similarly they will be updated locally when they are updated on S3 (note for devs: this is controlled by .data_version file in this directory).

```sh
make update-omop-data
```

Start the database container using:

```sh
make up-test-omop-trust1
```

This should not run any initialization scripts as the data volume already contains a populated database.

## PostgreSQL Read-Only User Management

This directory contains scripts and tools for managing PostgreSQL users with read-only permissions for the OMOP database in the FLIP project.

### Overview

The scripts implement security best practices for creating PostgreSQL users that can only perform SELECT operations on the OMOP database, preventing accidental data modification while enabling necessary read access for various services.

### Files

- `create_readonly_users.sql` - SQL script to create read-only users and roles
- `manage_readonly_users.py` - Python script for programmatic user management
- `setup_readonly_users.sh` - Bash script to automate the setup process
- `.env.readonly-users` - Environment variable template for user credentials

### Quick Start

1. **Ensure the OMOP database is running:**

   ```bash
   cd ../trust
   docker compose up omop-db -d
   ```

2. **Run the setup script:**

   ```bash
   cd omop-db
   ./setup_readonly_users.sh
   ```

3. **Update your application configuration** with the generated credentials.

### Created Users

The scripts create the following read-only users:

#### 1. `fl_service_reader`

- **Purpose**: Federated Learning service queries
- **Connection limit**: 10 concurrent connections
- **Query timeout**: 30 seconds
- **Use case**: FL aggregation and model training queries

#### 2. `api_service_reader`

- **Purpose**: API services (trust-api, data-access-api)
- **Connection limit**: 20 concurrent connections
- **Query timeout**: 10 seconds (fast API responses)
- **Use case**: Real-time API queries for cohort selection

#### 3. `data_analyst_reader`

- **Purpose**: Data analysis and research queries
- **Connection limit**: 5 concurrent connections
- **Query timeout**: 300 seconds (5 minutes for complex analysis)
- **Use case**: Ad-hoc analysis and reporting

#### 4. `omop_readonly_base` (role)

- **Purpose**: Base role with read-only permissions
- **Inherited by**: All read-only users
- **Permissions**: SELECT on all OMOP schema tables and sequences

### Security Features

#### Permissions Granted

- `CONNECT` to the `trustomopdb` database
- `USAGE` on the `omop` schema
- `SELECT` on all tables in the `omop` schema
- `SELECT` on all sequences in the `omop` schema (for pagination)

#### Permissions Explicitly Denied

- `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE` on tables
- `CREATE` on schema or database
- No administrative privileges

### Usage Examples

#### Connect with psql

```bash
# FL Service user
PGPASSWORD='your_password' psql -h localhost -p 5433 -U fl_service_reader -d trustomopdb

# API Service user
PGPASSWORD='your_password' psql -h localhost -p 5433 -U api_service_reader -d trustomopdb
```

#### SQLAlchemy Connection (Python)

```python
from sqlalchemy import create_engine

# FL Service connection
fl_engine = create_engine(
    "postgresql://fl_service_reader:password@localhost:5433/trustomopdb"
)
```

## Further Reading

- [Trust deployment overview](../README.md)
- [Contributing & Development Guide](CONTRIBUTING.md)
