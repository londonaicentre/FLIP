# AGENTS.md — FL Tutorials

Tutorials and shared dataset tooling for both FL backends. The tutorial *apps* are
built on the templates in `fl-apps/`; the images they run on are built from
`fl-services/` (see [`../fl-services/AGENTS.md`](../fl-services/AGENTS.md)).

| Path | Purpose |
|------|---------|
| `nvflare/image_*` | NVFLARE imaging tutorials — all Client-API apps |
| `nvflare/tabular_classification` | NVFLARE tabular (OMOP-only) EHR risk-prediction tutorial |
| `flower/{xray_classification,3d_spleen_segmentation*}` | Flower imaging tutorials |
| `flower/ehr_risk_prediction` | Flower tabular (OMOP-only) EHR risk-prediction tutorial |
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
make -C fl-tutorials sim-tutorial TUTORIAL=xray_classification FL_BACKEND=flower   # simulator, no containers
```

`sim-tutorial` means "no containers" on both backends. On NVFLARE it is an alias for
`run-tutorial`, which already runs the simulator; on Flower it runs `flwr run` against a local
SuperLink flwr starts itself (no SuperLink container, no SuperNodes, no fl-api, no Docker) where
`run-tutorial` brings up the standalone compose stack. The app code is identical either way — site identity comes from
`context.node_config`'s `partition-id`, falling back to the `SUPERNODE_NAME` a container sets
(`flip.flower.identity`). Under `LOCAL_DEV` both paths hand every client the same
`DEV_DATAFRAME`, so `partition_cohort` slices it per site; deployed, each trust's
data-access-api already serves its own cohort and no partitioning happens. The one thing the
Flower simulator supplies in place of fl-api's submit step is the evaluation tutorial's
checkpoint: `sim-tutorial.sh` passes `--run-config` pointing `flip-job-dir` at
`fl-tutorials/data/model_checkpoints` (fetched by `download-spleen-checkpoint`, part of
`download-spleen-data`) and `checkpoint` at `model.pt`, so
`make -C fl-tutorials sim-tutorial TUTORIAL=3d_spleen_segmentation_evaluation FL_BACKEND=flower`
runs unchanged app code too. The wrapper's exit status is the **run's** — it reads the run id off
the stream and asks the SuperLink (`flwr ls`) for the terminal status, because `flwr run --stream`
returns 0 whatever became of the run — and it refuses to start while a local SuperLink it did not
start still listens on `127.0.0.1:${FLWR_LOCAL_CONTROL_API_PORT:-39093}` (#1249): `flwr run . local`
reuses whatever is there, so a SuperLink another worktree left behind would run the app in *that*
checkout's environment with nothing in the output saying so. The stale-process cleanup deliberately
spares other checkouts (it matches this checkout's `flip-utils/` venv path, so the main checkout never
matches a worktree nested under it), so the fix is to stop the named pid (or run from that checkout);
an `ss` that cannot probe the port counts as taken, never as free. The tabular EHR tutorial maps
`DEV_DATAFRAME` only (`fl-tutorials/data/synthea/dataframe.csv`, from `download-synthea-data`).

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
make -C fl-tutorials convert-spleen-to-dicom        # full regeneration: NIfTI -> DICOM (deterministic, no root)
make -C fl-tutorials create-spleen-metadata-table   # full regeneration: DICOM -> metadata table
make -C fl-tutorials build-spleen-canonical         # the source_trust form that gets published/seeded
make -C fl-tutorials verify-spleen-dicom            # regenerated DICOMs <-> canonical tables, both ways
make -C fl-tutorials seed-spleen KIT=GSTT           # both halves of a RUNNING dev trust from the local tree
make -C fl-tutorials download-spleen-msd-raw        # the regeneration path's first step (the MSD archive)
```

**Scope differs per dataset, and it is not an oversight.** Spleen carries the whole chain from the
public MSD download; since #1261 its DICOM set is **regenerated locally and never published** (MSD is
open data) — the converter is deterministic (`datasets/utils/dicom_writer.py`, every UID and identity
a function of the case id), so the regenerated tree reproduces byte-for-byte and only the OMOP tables
+ `source/dicom_metadata.csv` go to `aicentreflip/trust-data`; a trust is seeded from the local tree
(`seed-spleen KIT=`, both halves local, which also lets a pull be proven before a data version is
tagged). **cxr carries only the OMOP conversion** — the synthetic chest X-rays, their DICOM write and
their metadata extraction live in the private `londonaicentre/xraycat` repo, so the in-tree
provenance chain starts at the published metadata table and its DICOM set is still re-hosted
(`dicom/cxr_project.tar.gz`).

See `fl-tutorials/datasets/README.md` ("OMOP mock-data generation") for both chains, the shared
contract in `datasets/utils/`, and the `download-spleen-msd-raw` regeneration-path first step.

## End-to-end on the platform

Running a tutorial through the full platform lifecycle (project → cohort → image pull →
training → results) is the `e2e_smoke` harness, driven from flip-api — see
[`../flip-api/AGENTS.md`](../flip-api/AGENTS.md#end-to-end-smoke-test). The spleen
tutorials additionally need the data-enrichment label upload documented there.
