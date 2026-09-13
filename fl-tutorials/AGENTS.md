# AGENTS.md — FL Tutorials

Tutorials and shared dataset tooling for both FL backends. The tutorial *apps* are
built on the templates in `fl-apps/`; the images they run on are built from
`fl-services/` (see [`../fl-services/AGENTS.md`](../fl-services/AGENTS.md)).

| Path | Purpose |
|------|---------|
| `nvflare/image_*` | NVFLARE tutorials — all Client-API apps |
| `flower/{xray_classification,3d_spleen_segmentation*}` | Flower tutorials |
| `datasets/` | Shared dataset tooling (download/derive/enrich), one copy for both backends |
| `datasets/utils/` | The OMOP CDM contract shared by the per-dataset generation chains (#1092): schemas, concept mappings, per-project surrogate-key blocks (`omop_ids.py`), and the one verification gate (`verify_omop_tables.py --project <name>`) |
| `data/` | Gitignored output of the dataset targets |
| `tests/` | CPU-only pytest over the tutorial transform chains (#871) plus a static `min_clients` wiring guard covering `fl-apps/flower` |

```bash
make -C fl-tutorials test   # ruff over fl-tutorials/ + the CPU-only suite (no GPU/dataset/FL image)
```

## Running the tutorials

The NVFLARE tutorials live in `fl-tutorials/` and are all **Client-API** apps (the legacy Executor
tutorials, templates and their Docker `testing/` harness are removed; the pre-rename `*_client_api`
job-type names survive only as accepted aliases for models created before the rename). Each tutorial carries a `.env.app` and a `job.py` driving a FLIP recipe;
`make run` delegates to `make sim`, which runs the NVFLARE simulator (SimEnv) in the flip-utils venv
with the `full` ML extra (needs a GPU; per-tutorial `make export` builds the full job config with no
GPU). From the repo root:

```bash
make -C fl-tutorials list-tutorials
make -C fl-tutorials download-xray-data                  # xray dataset (HF); spleen: download-spleen-data
make -C fl-tutorials run-tutorial TUTORIAL=xray_classification
make -C fl-tutorials run-all-tutorials                   # every tutorial (heavy; stops on first failure)
```

To iterate on the FL images, `make build-fl` builds them locally as `:dev` (see `fl-services/nvflare/README.md`);
run the stack on them with `make up DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev`.

The tutorials' mock OMOP data is generated in-tree, per dataset, under `fl-tutorials/datasets/`
(FLIP#1092). Each project is reproducible without root from a pinned published metadata table and
verified against the published export by one shared gate
(`datasets/utils/verify_omop_tables.py --project <name>`):

```bash
make -C fl-tutorials reproduce-spleen-omop          # fetch -> build -> verify, chained
make -C fl-tutorials reproduce-cxr-omop             # same three for cxr_project
make -C fl-tutorials fetch-spleen-metadata-table    # or step by step: pinned metadata table
make -C fl-tutorials build-spleen-omop-tables       # -> omop/<trust>/spleen_project/*.csv
make -C fl-tutorials verify-spleen-omop-tables      # diff against the published export
make -C fl-tutorials convert-spleen-to-dicom        # spleen-only full regeneration (workstation/root)
make -C fl-tutorials create-spleen-metadata-table   # spleen-only full regeneration
```

**Scope differs per dataset, and it is not an oversight.** Spleen carries the whole chain from the
public MSD download. **cxr carries only the OMOP conversion** — the synthetic chest X-rays, their
DICOM write and their metadata extraction live in the private `londonaicentre/xraycat` repo, so the
in-tree provenance chain starts at the published metadata table. Neither regeneration path
reproduces the published export byte-for-byte anyway (spleen re-synthesises patient identities on
every run), which is why the gate's fixed input is the published metadata table rather than the
images.

See `fl-tutorials/datasets/README.md` ("OMOP mock-data generation") for both chains, the shared
contract in `datasets/utils/`, and the `download-spleen-msd-raw` regeneration-path first step.

## End-to-end on the platform

Running a tutorial through the full platform lifecycle (project → cohort → image pull →
training → results) is the `e2e_smoke` harness, driven from flip-api — see
[`../flip-api/AGENTS.md`](../flip-api/AGENTS.md#end-to-end-smoke-test). The spleen
tutorials additionally need the data-enrichment label upload documented there.
