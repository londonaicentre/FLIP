# CLAUDE.md — FLIP Documentation

## Documentation Index (read on demand)

| File | Topic |
|------|-------|
| `source/1_overview.rst` | Project overview, architecture, motivation |
| `source/2_components.rst` | Component descriptions (API, UI, trust services, FL nodes) |
| `source/3_sys-admin.rst` | System administration, deployment, auth configuration |
| `source/4_user-guides.rst` | User-facing workflows and guides |
| `source/5_api_reference.rst` | REST API endpoint reference |
| `source/6_faqs.rst` | Frequently asked questions |
| `source/7_glossary.rst` | Terminology definitions |

## Sub-docs

| File | Topic |
|------|-------|
| `source/components/component-fl-nodes.rst` | FL training nodes (NVFLARE/Flower) |
| `source/components/component-user-roles.rst` | User roles and permissions |
| `source/user-guides/` | User guide files |

## How to Read

When implementing a feature that touches documentation, read the relevant `.rst` file(s) above. These are ReStructuredText format used by Sphinx for ReadTheDocs builds.

## Build Commands

```bash
cd docs && make clean    # Clean built docs
cd docs && make docs     # Build Sphinx HTML documentation
```
