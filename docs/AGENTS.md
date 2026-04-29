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

# AGENTS.md — docs

## Scope

Applies to Sphinx documentation under `docs/`.

## Documentation Map

- `source/1_overview.rst`: project overview and architecture.
- `source/2_components.rst`: component descriptions.
- `source/3_sys-admin.rst`: system administration, deployment, and auth configuration.
- `source/4_user-guides.rst`: user-facing workflows.
- `source/5_api_reference.rst`: REST API reference.
- `source/6_faqs.rst`: FAQs.
- `source/7_glossary.rst`: terminology.
- `source/components/`: detailed component pages.
- `source/user-guides/`: user guide pages.

## Commands

Run from `docs/`:

```bash
make clean
make docs
```

## Conventions

- Documentation is ReStructuredText for Sphinx/ReadTheDocs.
- Update docs when changing user workflows, deployment instructions, env vars, service architecture, auth/roles, or API
  behavior.
- Prefer linking to canonical service/deployment docs rather than duplicating long operational procedures.
