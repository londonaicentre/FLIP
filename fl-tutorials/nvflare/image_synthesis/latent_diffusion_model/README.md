# Latent Diffusion Model — FLIP tutorial

This tutorial trains a two-stage latent diffusion model (a variational autoencoder with a GAN
discriminator, then a diffusion model over the frozen autoencoder's latent space) using the
**NVFLARE Client API** (`nvflare.client`). The job is defined entirely in Python via
`FlipDiffusionRecipe` — no hand-written JSON configs required. The code is entirely based on
`MONAI` functions.

It replaced the retired Executor-API tutorial of the same name (removed along with the other
legacy-syntax NVFLARE tutorials). The training and validation maths are identical to that legacy
flow; what changed is the NVFLARE plumbing — noted here for anyone migrating an old app:

- **One client script serves four task names.** The job's two training phases (`train_ae`,
  `train_dm`) and two cross-site validation passes (`validate_ae`, `validate_dm`) all run through a
  single `InProcessClientAPIExecutor` driving `app_files/trainer.py`. The single-name
  `flare.is_train()` / `flare.is_evaluate()` predicates cannot distinguish the two train phases, so
  the script dispatches on `nvflare.client.api.get_task_name()`.
- **`validator.py` is a plain module**, not an Executor: it holds the validation passes and the
  latent-geometry helpers (`derive_new_latent_shape`, `build_inferer`) that `trainer.py` imports.
- **The DP filter is live.** Training results carry a `FlipMetaKey.STAGE` meta naming the modules
  the phase trained (`autoencoder`+`discriminator`, then `diffusion_model`), which scopes the
  `StagePercentilePrivacy` percentile cutoff per stage. The legacy tutorial never stamped this
  meta, so its filter passed every update through unfiltered.

Validation provides L1 loss and SSIM for stage 1 and the noise-prediction loss for the diffusion
model.

## Compatible job type

These files are compatible with `JOB_TYPE=diffusion_model` in the base application
([`fl-apps/nvflare/diffusion_model/`](../../../../fl-apps/nvflare/diffusion_model/)).

## Rounds configuration

Each stage has its own round counts in `app_files/config.json`: `GLOBAL_ROUNDS_AE` /
`GLOBAL_ROUNDS_DM` (federated rounds — re-read by the `ScatterAndGatherLDM` controllers at start,
so they win over whatever the recipe baked in) and `LOCAL_ROUNDS_AE` / `LOCAL_ROUNDS_DM` (local
epochs per round).

## Base-image dependency (torchvision)

Unlike most tutorials, this one needs `torchvision` at runtime: the perceptual loss uses `lpips`,
which calls `torchvision.ops` operators (e.g. `nms`). Those must be built against the **same
torch** as the `flare-fl-base` image (pinned `torch>=2.11`, cu128, in
[`flip-utils/pyproject.toml`](../../../../flip-utils/pyproject.toml)). A base image whose
`torchvision` predates that pin fails at runtime with
`RuntimeError: operator torchvision::nms does not exist`. (The `app_files/requirements.txt` lists
`torchvision` too, but that file is a dependency *spec* — the runtime deps come from the base
image, not from installing it per job.)

## FLIP-specific values

`FLIP_PROJECT_ID` and `FLIP_QUERY` are read from environment variables (set stubs in `.env.app`).
They are NOT passed as CLI flags because the SQL query contains spaces that don't survive
argparse whitespace-splitting. The trainer reads the query from `config_fed_client.json` at
runtime via `load_query()`.

## How to run

### Export (primary — no GPU needed)

Produces a complete NVFLARE job directory under `./fl_job/flip_diffusion/` including:
- `meta.json` with `custom_props.model_id` (dev UUID for local use)
- `app/config/config_fed_server.json` and `config_fed_client.json`
- `app/custom/` with the bundled `flip/` package and all user app files staged alongside it

```bash
make export
```

### SimEnv local simulation (requires GPU + data)

```bash
make -C fl-tutorials download-spleen-data   # once, from the repo root
make -C fl-tutorials run-tutorial TUTORIAL=latent_diffusion_model
# or, from this directory:
make run NUM_ROUNDS_AE=1 NUM_ROUNDS_DM=1 N_CLIENTS=2
```

The spleen CT volumes are only a convenient openly-licensed 3D dataset — the labels are unused;
any 3D CT NIfTI collection works.

### Against a running FLIP stack

Upload `app_files/` as the model files of a project whose `config.json` declares
`"job_type": "diffusion_model"` (the e2e smoke does this with
`MODEL_FILES_DIR=fl-tutorials/nvflare/image_synthesis/latent_diffusion_model/app_files
QUERY_FILE=...`).
