# CLAUDE.md — FLIP Documentation

## Documentation Index (read on demand)

| File | Topic |
|------|-------|
| `source/overview.rst` | Project overview, architecture, motivation |
| `source/components.rst` | Component descriptions (API, UI, trust services, FL nodes) |
| `source/sys-admin.rst` | System administration, deployment, auth configuration |
| `source/user-guides.rst` | User-facing workflows and guides |
| `source/api-reference.rst` | REST API endpoint reference |
| `source/deploy-flip.rst` | Deployment instructions (central hub, TRE, on-prem) |
| `source/working-with-flip-apps.rst` | Building FL apps (NVFLARE / Flower) |
| `source/flip-workflow.rst` | End-to-end FLIP workflow |
| `source/faqs.rst` | Frequently asked questions |
| `source/glossary.rst` | Terminology definitions |

## Sub-docs

| Directory | Topic |
|-----------|-------|
| `source/components/` | Per-component deep dives (FL nodes, XNAT, OMOP, logging stack, architecture overview) |
| `source/sys-admin/` | Admin tasks (user roles, project/user management, platform support) |
| `source/user-guides/` | User guide files |
| `source/deploy-flip/` | Per-target deployment guides (central hub, TRE, on-prem) |
| `source/working-with-flip-apps/` | Step-by-step FLARE / Flower app authoring |

## How to Read

When implementing a feature that touches documentation, read the relevant `.rst` file(s) above. These are ReStructuredText format used by Sphinx for ReadTheDocs builds.

## Build Commands

```bash
cd docs && make clean    # Clean built docs
cd docs && make docs     # Build Sphinx HTML documentation
```
