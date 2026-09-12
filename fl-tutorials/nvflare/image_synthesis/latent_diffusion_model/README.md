# Latent Diffusion Model — FLIP tutorial

This tutorial trains a **`DiffusionModelUNet` inside a frozen autoencoder's latent space** on 2-D
chest X-rays, using the **NVFLARE Client API** (`nvflare.client`). The job is defined entirely in
Python via `FlipFedAvgRecipe` — no hand-written JSON configs required. The code is entirely based on
`MONAI` functions.

**The autoencoder is not trained here — you supply it.** That is the one thing that makes this
tutorial different from its two siblings, and it is why it needs a model uploaded alongside its app
files.

It is one of three tutorials that between them cover generative image synthesis on FLIP:

| Tutorial | Trains | Operates on | Needs an uploaded model? |
| --- | --- | --- | --- |
| [`autoencoder`](../autoencoder/) | `AutoencoderKL` + `PatchDiscriminator` | images | no |
| [`diffusion_model`](../diffusion_model/) | `DiffusionModelUNet` | images directly (pixel space) | no |
| **`latent_diffusion_model`** (this one) | `DiffusionModelUNet` | latents of a frozen autoencoder | **yes** — the autoencoder |

Each is an **independent single-stage job**. This tutorial used to be a single two-stage job that
trained the autoencoder and then the diffusion model in one run; the two halves are now separate, so
you can train an autoencoder once and re-use it across many diffusion runs, or bring one you already
have.

Validation reports the noise-prediction MSE on each site's held-out split.

**New to this?** Read [`diffusion_model`](../diffusion_model/) first — same denoising objective,
without the frozen autoencoder or any of the latent machinery.

## Data

The same chest X-ray cohort and loading path as
[`xray_classification`](../../image_classification/xray_classification/): DICOM files fetched per
accession, read through the pinned `PydicomReader` chain in `app_files/transforms.py` (copied from
that tutorial) and resized to **256×256**, which the autoencoder compresses to a 64×64 latent with 3
channels.

```bash
make -C fl-tutorials download-xray-data
```

The autoencoder you supply must have been trained on the same kind of image at the same size — in
practice, by the [`autoencoder`](../autoencoder/) tutorial on the same cohort.

## Compatible job type

These files are compatible with `JOB_TYPE=standard` in the base application
([`fl-apps/nvflare/standard/`](../../../../fl-apps/nvflare/standard/)). The required upload set is
`trainer.py`, `config.json`, `models.py`; `transforms.py`, `latent_utils.py` **and the autoencoder
checkpoint** are uploaded alongside them.

Note this tutorial no longer uses `JOB_TYPE=diffusion_model`. That job type is the platform's older
*two-stage* autoencoder-then-diffusion job; it remains registered and working, but none of these three
tutorials uses it.

## Getting the autoencoder (do this first)

The frozen encoder is declared by `SERVER_CHECKPOINT` in `app_files/config.json` and must exist as
`app_files/pretrained_autoencoder.pt`. A finished [`autoencoder`](../autoencoder/) run does not hand
you one directly — its checkpoint is an NVFLARE persistor file wrapping the *whole* training network
(autoencoder **and** discriminator). `process_tools/extract_autoencoder.py` converts it:

```bash
# 1. Train an autoencoder (or use one you already have)
make -C fl-tutorials run-tutorial TUTORIAL=autoencoder

# 2. Extract the autoencoder-only checkpoint from that run's result
make prepare-checkpoint RAW_CHECKPOINT=<path to that run's .pt>
```

`prepare-checkpoint` unwraps the persistor envelope (the state dict sits under a `"model"` key),
keeps only `autoencoder.*`, and writes `app_files/pretrained_autoencoder.pt`. It is a no-op if that
file already exists, and `make run` depends on it. Dropping `discriminator.*` is about file size and
a clean load log rather than correctness — the persistor loads `strict=False`, so leftover keys would
merely be reported as unexpected.

**That file is the only checkpoint you upload.** There is no diffusion-model checkpoint to provide,
empty or otherwise: the server builds the full network from `models.py` — autoencoder plus a freshly
initialised diffusion model — and fills in the autoencoder half from your upload before round 0. The
two models stay separate everywhere else: each is trained by its own job, and the autoencoder's
weights are never updated here.

### Two ways this handoff breaks silently

Both are worth knowing about, because **neither raises an error** — the persistor's load is
`strict=False`, so a mismatch is tolerated and the diffusion model simply trains against a
randomly-initialised encoder while reporting entirely plausible losses:

1. **The submodule name.** Both networks must call the submodule `autoencoder`, so its parameters are
   keyed `autoencoder.*` in both state dicts. Renaming it in either `models.py` breaks the handoff.
2. **`net_config.stage_1`.** It must be identical to the `autoencoder` tutorial's block — it *is* the
   architecture the checkpoint was trained for.

`fl-tutorials/tests/test_image_synthesis_config_parity.py` guards both statically. At run time, check
the server log for `Loaded backbone into initial global model from …` and confirm the **missing-key
count** covers only the diffusion model (126 of the network's 444 tensors are the autoencoder's); a
larger count is the symptom of either failure above.

## How the frozen autoencoder reaches the clients

Clients need the autoencoder to encode images into latents, but they never receive the file:

1. `SERVER_CHECKPOINT` names the file. `InitialCheckpointPTModelPersistor` loads it **server-side**
   into the round-0 global model, so round 0 broadcasts a full model — your autoencoder plus a
   freshly-initialised diffusion model.
2. `AGGREGATE_ONLY_REGEX` (`^diffusion_model\.`) then keeps only the diffusion model on the wire.
   Clients return only `diffusion_model.*` diffs (`KeepOnlyVars` — 318 of 444 tensors), the server
   broadcasts only those after round 0 (`TrimBroadcastVars`), and each client rebuilds the full model
   from its cached round-0 broadcast (`ReconstructFullModel`).

In production the checkpoint never enters the job bundle at all: flip-api's bundler diverts any file
named by `SERVER_CHECKPOINT` to `<bucket>/server_checkpoints/`, and the FL API stages it to
`<SERVER_CHECKPOINT_ROOT>/<model_id>/` on a hub-local volume no client can read. (Locally, the export
produces a single shared `app/` dir, so the checkpoint sits in the one `custom/` directory — which is
exactly where the persistor looks for a simulator run.)

### `AGGREGATE_ONLY_REGEX` is not only a bandwidth saving

It is also a **privacy** control. `PercentilePrivacy` computes its cutoff over *all* variables
concatenated together, not per-variable, and zeroes everything below the 10th percentile by default.
The frozen autoencoder is ~10% of this network's parameters and its diffs are exactly zero, so leaving
them in the update puts that cutoff at or immediately next to zero — at which point the DP
sparsification stops discarding anything and quietly degrades to the `gamma` clip alone. Keeping the
autoencoder out of the update is what keeps the cutoff meaningful. (With a larger autoencoder relative
to the diffusion model, the effect is starker still.)

## The latent scale factor (`LATENT_SCALE_FACTOR`)

The diffusion model trains on latents normalised by a scale factor. By default that factor is derived
from a **local** batch, which means every site derives a slightly different one and their updates are
not averaging quite-comparable models. With a frozen autoencoder the factor is a fixed property of
that autoencoder and the data, so you can pin it:

```json
"LATENT_SCALE_FACTOR": 1.234
```

Set it and every site uses that exact value — **recommended for any real multi-site run**. Leave it
`null` and behaviour is identical to the historical per-batch derivation. Either way the resolved
value is logged, so you can read the derived numbers off a first run and pin the result.

## Seeing what it generates (`SAVE_DEBUG_SAMPLES`)

Loss curves are a poor judge of a generative model — the noise-prediction MSE barely separates a model that generates radiographs from one that generates plausible texture. Set

```json
"SAVE_DEBUG_SAMPLES": true,
"DEBUG_SAMPLES_MAX": 8
```

in `config.json` and the client writes PNGs to `app_files/debug_samples/` **inside its own job
workspace** — per client, per run, gitignored.

`samples_....png` tiles a batch of generated images, written by the `validate` task.
Sampling is a full reverse diffusion (one forward pass per training timestep), which is why it runs
there and not once per epoch.

**These samples are also the best check that the frozen autoencoder actually loaded.** Sampling is
the only thing in this job that runs the autoencoder's *decoder*, and the checkpoint load is
`strict=False` — so a mismatched or missing checkpoint (see "Two ways this handoff breaks silently"
above) trains happily, reports a falling loss, and shows up here as noise. If the first round's
samples are structured at all, the latent space is real.

Nothing about this puts an image on the wire. The files are written beside the running training
script and no code path reads them back, adds them to an `FLModel` or hands them to the metrics
writer — the Client API's `SummaryWriter` carries scalars only, so it could not take one anyway. An
image leaves the site only if a person deliberately copies it out, which at a real trust is a
disclosure decision like any other, not something the job can do by itself.

It ships **off**, and is meant for local runs. Left on at a trust it accumulates patient-derived
images on that trust's disk, round after round, with no retention policy attached.

## Rounds configuration

`app_files/config.json` carries `GLOBAL_ROUNDS` (federated rounds) and `LOCAL_ROUNDS` (local epochs
per round). These names are load-bearing: with a single local-rounds key, the FL API requires it to be
called exactly `LOCAL_ROUNDS` and rejects the job otherwise. (The old two-stage job's
`GLOBAL_ROUNDS_AE`/`GLOBAL_ROUNDS_DM` pairs no longer apply.)

## FLIP-specific values

`FLIP_PROJECT_ID` and `FLIP_QUERY` are read from environment variables (set stubs in `.env.app`).
They are NOT passed as CLI flags because the SQL query contains spaces that don't survive argparse
whitespace-splitting. The trainer reads the query from `config_fed_client.json` at runtime via
`load_query()`.

## How to run

### Export (primary — no GPU needed)

```bash
make export
```

Writes a complete NVFLARE job to `./fl_job/flip_fedavg/` (`meta.json`, `app/config/`,
`app/custom/`). This is the fastest way to verify the job wiring, and deliberately does **not**
require the checkpoint. Worth inspecting in the export:

- `config_fed_client.json` — `keep_only_trainable_vars` ordered **before** `percentile_privacy`
  (that order is what keeps the DP cutoff off the frozen autoencoder's zeros), plus
  `reconstruct_full_model`.
- `config_fed_server.json` — `trim_broadcast_to_trainable` and `InitialCheckpointPTModelPersistor`.

### Local simulation (requires a GPU + the dataset + the checkpoint)

```bash
make -C ../../.. download-xray-data        # once
make prepare-checkpoint RAW_CHECKPOINT=... # see "Getting the autoencoder" above
make run                                   # delegates to `make sim`
```

Or via the shared harness:

```bash
make -C fl-tutorials run-tutorial TUTORIAL=latent_diffusion_model
```

Knobs: `NUM_ROUNDS` (default 1), `N_CLIENTS` (default 2) — override per invocation
(`make run NUM_ROUNDS=2`) or in `.env.app`. In simulation the two sites train on different halves of
the dev dataset; in production each trust's cohort is already its own.

### Clean

```bash
make clean
```
