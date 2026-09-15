# CLAUDE.md — FLIP Documentation

## Documentation Index (read on demand)

| File | Topic |
|------|-------|
| `source/overview.rst` | Project overview, architecture, motivation |
| `source/components.rst` | Toctree of the component pages (Overview, Central Hub, FL nets, Trust APIs, OMOP, XNAT, PACS, Logging Stack) |
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
| `source/components/` | Per-component deep dives: `overview` (component map), `component-central-hub` (hub services + the generated AWS diagram), `component-fl-nets` (nets, scheduler, job types, config, privacy filters), `component-trust-apis` (trust-api / imaging-api / data-access-api), OMOP, XNAT, PACS, logging stack |
| `source/sys-admin/` | Admin tasks (user roles, project/user management, platform support) |
| `source/user-guides/` | User guide files |
| `source/deploy-flip/` | Per-target deployment guides (central hub, TRE, on-prem) |
| `source/working-with-flip-apps/` | Step-by-step FLARE / Flower app authoring |

## How to Read

When implementing a feature that touches documentation, read the relevant `.rst` file(s) above. These are ReStructuredText format used by Sphinx for ReadTheDocs builds.

## Build Commands

```bash
cd docs && make clean    # Clean built docs (also drops source/assets/generated/)
cd docs && make docs     # Build Sphinx HTML documentation
```

The build needs graphviz (`dot`) on PATH: `conf.py`'s `builder-inited` hook renders the Central Hub AWS
diagrams from `deploy/providers/AWS/architecture/central_hub.py` into the gitignored
`source/assets/generated/` and fails loudly without it (ReadTheDocs installs it via `build.apt_packages`,
the docs CI job via `apt-get`). `FLIP_DOCS_SKIP_DIAGRAMS=1 make docs` builds text-only on a host without
graphviz — with a warning, and missing-image warnings on the Central Hub page. Pages renamed in FLIP#364
keep their old URLs through `sphinx-reredirects` (`redirects` in `conf.py`).
