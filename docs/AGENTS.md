# AGENTS.md — FLIP Documentation

## Documentation Index (read on demand)

| File | Topic |
|------|-------|
| `source/overview.rst` | Project overview, architecture, motivation |
| `source/components.rst` | Toctree of the component pages (Overview, Central Hub, FL nets, Trust APIs, OMOP, XNAT, PACS, Logging Stack) |
| `source/sys-admin.rst` | System administration, deployment, auth configuration |
| `source/user-guides.rst` | User-facing workflows and guides |
| `source/api-reference.rst` | Auto-generated Python reference for `flip-utils` only (sphinx-autoapi; the hub and trust services are applications, not libraries); the REST endpoint table lives in `source/components/component-central-hub.rst` |
| `source/deploy-flip.rst` | Deployment section index: the Central Hub, a trust node, local development |
| `source/working-with-flip-apps.rst` | Building FL apps (FLARE / Flower) |
| `source/flip-workflow.rst` | End-to-end FLIP workflow |
| `source/faqs.rst` | Frequently asked questions |
| `source/glossary.rst` | Terminology definitions |
| `source/security.rst` | How FLIP addresses security at each layer (network, cloud estate, identity, clinical-data boundary, the FL process itself, storage/transport, supply chain) — written for partner trusts and information governance leads |
| `source/governance-and-compliance.rst` | Who decides what in FLIP and how the platform maps onto NHS assurance frameworks — written for information governance/Caldicott functions and research ethics committees; companion to `security.rst` |
| `source/paper.rst` | The FLIP paper (arXiv 2609.36001): citation, plus the guides reproducing its experiments under `source/paper/` (Ark+ fine-tuning, platform overhead) — last section of the Documentation toctree |
| `source/troubleshooting.rst` | Troubleshooting section index (symptom → cause → fix pages under `source/troubleshooting/`) |
| `source/release-notes.rst` | Where release notes live (GitHub Releases) and how releases are versioned |

## Sub-docs

| Directory | Topic |
|-----------|-------|
| `source/components/` | Per-component deep dives: `overview` (component map), `component-central-hub` (hub services, deployment-agnostic), `component-fl-nets` (nets, kit slots, scheduler, GPUs, quiesce), `component-fl-jobs` (job types, required files, config.json, meta.json, `flip.*` calls), `component-fl-privacy` (privacy filters, site policy, governance document), `component-trust-apis` (trust-api / imaging-api / data-access-api), OMOP, XNAT, PACS, logging stack |
| `source/sys-admin/` | Admin tasks: project/user management, user roles, platform support, upgrading sites, backup and restore |
| `source/user-guides/` | Researcher guides: `getting-started`, `user-common` (landing) with `user-account` / `user-projects` / `user-models` / `user-training`, data enrichment, trust admin |
| `source/deploy-flip/` | Deployment guides: `deploy-central-hub` (shared hub operations) with child `deploy-central-hub-aws-lza` (the architecture and the generated diagrams; the old `deploy-central-hub-aws` URL redirects there); `deploy-trust-node` (landing) with `onboarding-a-trust` (the shared runbook) and the per-platform pages `deploy-flip-node-on-prem` (Docker Compose), `deploy-flip-node-azure` (Azure VM), `deploy-flip-node-kubernetes` (Helm), `deploy-flip-node-in-tre`; `deploy-local-dev` (the one-host `make up` walkthrough) |
| `source/working-with-flip-apps/` | `choosing-an-fl-framework`, the paired FLARE / Flower app-authoring pages, `wrap-model-for-deployment` (MAP tutorial) and `map-toolchain-reference` |
| `source/troubleshooting/` | Symptom-first troubleshooting: FL training/projects, Central Hub on AWS, trust on Compose, trust on Kubernetes (index: `source/troubleshooting.rst`) |

## Paired pages

`source/working-with-flip-apps/create-flip-app-from-flare.rst` and `create-flip-app-from-flower.rst` are twins: same section order, same SDK touchpoints, each ending in a `Common pitfalls` list, and each carrying a note at the top pointing at the other. When one page changes — a new SDK call, a renamed run-config key, a new pitfall, a restructured section — check whether the other needs the same change, and make both edits in the same PR. The backend-specific facts differ (the comparison table at the top of the FLARE page summarises them), but the *shape* of the two pages should not drift apart.

## How to Read

When implementing a feature that touches documentation, read the relevant `.rst` file(s) above. These are ReStructuredText format used by Sphinx for ReadTheDocs builds.

## Build Commands

```bash
cd docs && make clean    # Clean built docs (also drops source/assets/generated/)
cd docs && make docs     # Build Sphinx HTML documentation
cd docs && make test     # pytest over tests/ + ruff over scripts/, tests/, conf.py
```

The build needs graphviz (`dot`) on PATH: `conf.py`'s `builder-inited` hook renders the Central Hub AWS
diagrams from `deploy/providers/AWS/architecture/central_hub.py` into the gitignored
`source/assets/generated/` and fails loudly without it (ReadTheDocs installs it via `build.apt_packages`,
the docs CI job via `apt-get`). `FLIP_DOCS_SKIP_DIAGRAMS=1 make docs` builds text-only on a host without
graphviz — with a warning, and missing-image warnings on the Central Hub page. Pages renamed in FLIP#364
keep their old URLs through `sphinx-reredirects` (`redirects` in `conf.py`).

The user-guide GIFs are **not tracked in git** (FLIP#1236). A second `builder-inited` hook in `conf.py` fetches
them from the public HF dataset `aicentreflip/docs-gifs` (`scripts/fetch_docs_gifs.py`) into
`source/assets/generated/gifs/<cat>/<name>.gif`, which is where the rst figures point and where
`flip-ui/scripts/videos-to-gifs.sh` writes a local recording. There is no pin (FLIP#1547): the build picks the
revision from ReadTheDocs' build variables — a release tag `vX.Y.Z` (and git `main` / RTD `stable`, via the
`pyproject.toml` version) gets the dataset tag of that name, a PR preview #N gets dataset branch `pr-N` if
someone published a hand recording there, everything else gets dataset `main` (the newest recording) — and falls
back to `main` when the first choice is missing. It resolves that ref to a commit and reads every file at it,
verifying sha256 + size against `manifest.json`. A fetch failure is a `SphinxError` naming the URL;
`FLIP_DOCS_SKIP_GIF_FETCH=1` builds text-only (and is how a local recording is previewed — without it the
published bytes win per file). `FLIP_DOCS_GIFS_REPO` / `FLIP_DOCS_GIFS_REVISION` override the dataset / the
revision. Recordings are published by `.github/workflows/regenerate_docs_gifs.yml` on every UI push to `develop`
(`scripts/publish_docs_gifs.py --branch main`, one commit, nothing back to git); `release.yml`'s `docs-gifs`
job tags the recording a release ships (`scripts/freeze_docs_gifs.py`) before it pushes the git tag. The card
is `scripts/docs_gifs_card.md`. Adding a GIF is a spec + an rst figure in one PR; it renders once that PR
reaches `develop` and is recorded — `tests/test_docs_gifs_wiring.py` keeps figures and specs in step.

GIFs that need a live trust (the MONAI Label walkthrough: GPU + the real XNAT OHIF viewer) are listed in
`scripts/live-recorded-gifs.txt` instead of having a spec; `expected_gifs()` joins them to the spec set, so the
wiring test and the publisher treat them alike. They are cut by `make -C trust/monailabel-demo demo-gifs` and
published by hand to dataset branch `pr-<N>` of the PR that re-cuts them (its preview shows them); a CI publish,
which cannot record them, carries each one forward from the merged PR's `pr-<N>`, else from `main`
(`--carry-from`, sha256-checked against that ref's manifest).
