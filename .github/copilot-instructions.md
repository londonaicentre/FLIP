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

# This file contains instructions for using GitHub Copilot

# Project Context

This project is the FLIP, a system designed to manage and analyze federated learning (FL) tasks and models. It includes features for managing projects, models, logs, and user interactions.
The project is structured as a Monorepo with multiple components, including an API, frontend, and deployment scripts.

The main folders are:
- `flip-api`: Contains the backend API code for the Centralhub, which coordinates all the services and is the main point of contact with the database containing all user related data.
- `flip-ui`: Contains the frontend code, including user interfaces, components, and styles. It's the only JavaScript/TypeScript code in the project.
- `trust`: Contains the code for the trust service, which manages trust-related operations and APIs which manage the patient data.
- `trust/trust-api`: Contains the trust API code, which polls the Central Hub for tasks (outbound only) and dispatches them within the trust.
- `trust/imaging-api`: Contains the imaging API code, which handles image-related operations and APIs. It is called by the trust-api and by the trust's FL clients, authenticated with the trust-internal service key.
- `trust/data-access-api`: Contains the data access API code, which handles data access operations and APIs. It is called by the trust-api, the imaging-api and the trust's FL clients, authenticated with the trust-internal service key.
- `flip-utils`: The `flip` Python package that FL training code calls; it is baked into the FL client and server images.
- `fl-services`, `fl-apps`, `fl-tutorials`: The FL Docker services, app templates and tutorials, for both NVIDIA FLARE and Flower.
- `deploy`, `docs`: Central Hub deployment (Docker Compose, and Terraform under `deploy/providers/AWS`) and the Sphinx documentation.

All APIs are created using FastAPI, and the project uses the SQLModel library for database interactions. The project is designed to be modular, with each component handling specific functionalities related to federated learning and trust management.

All Python code is managed using `UV` as package manager, so every Python project has a `pyproject.toml` and a `tests` folder split into `tests/unit/` and `tests/integration/`. The project uses `pytest` for testing and `uv run` for running the application.

# Copilot Instructions

- Use Makefile to automate common tasks and commands. Every sub-repo has its own Makefile, so make sure to run the commands in the correct sub-repo.
- Documentation follows the [Google style guide](https://google.github.io/styleguide/pyguide.html) for Python. The documentation generator is [Sphinx](https://www.sphinx-doc.org/en/master/).
- Put Python code in the service's package directory (under `src/` in flip-api and trust/omop-db, at the service root in the three trust APIs, `flip/` in flip-utils). A test goes in `tests/integration/` only if it touches a real backing service (database, AWS, a running sibling API); otherwise it goes in `tests/unit/`.
- Use the `uv` package manager for managing dependencies and running the application.
- Use the `pytest` framework for writing and running tests.
- Use SQLModel for database interactions, and follow the project's database schema and models.
- Use FastAPI for creating APIs, and follow the project's API structure and endpoints.
- Use .vscode for development, and follow the project's .vscode settings for linting and formatting.
- Use the `aws` CLI for AWS interactions, and follow the project's AWS configuration and profiles.
- Use the docker compose and open tofu for managing project deployment and infrastructure.
- Use github actions for CI/CD, and follow the project's GitHub Actions workflows.
- Add new environment variables to `.env.development.example` (the tracked template `.env.development` is copied from), and document them in `CONTRIBUTING.md` and `docs/source/sys-admin.rst`.
- Use AWS Secrets Manager for managing secrets, and follow the project's secrets management practices in production.
- Follow the project's coding conventions, defined in `AGENTS.md` ("Code Style & Conventions") and `CONTRIBUTING.md`; lint rules live in each `pyproject.toml`.
