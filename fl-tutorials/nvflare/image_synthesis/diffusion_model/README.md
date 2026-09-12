# Diffusion Model (pixel space) — FLIP tutorial

This tutorial trains a **`DiffusionModelUNet` that denoises images directly** — diffusion in pixel
space, with no autoencoder in the loop — on 2-D chest X-rays, using the **NVFLARE Client API**
(`nvflare.client`). The job is defined entirely in Python via `FlipFedAvgRecipe` — no hand-written
JSON configs required. The code is entirely based on `MONAI` functions.

It is one of three tutorials that between them cover generative image synthesis on FLIP:

| Tutorial | Trains | Operates on | Needs an uploaded model? |
| --- | --- | --- | --- |
| [`autoencoder`](../autoencoder/) | `AutoencoderKL` + `PatchDiscriminator` | images | no |
| **`diffusion_model`** (this one) | `DiffusionModelUNet` | images directly (pixel space) | no |
| [`latent_diffusion_model`](../latent_diffusion_model/) | `DiffusionModelUNet` | latents of a frozen autoencoder | **yes** — the autoencoder |

**Read this one before the latent tutorial.** It is the simpler of the two diffusion tutorials: same
denoising objective, none of the latent machinery. The latent tutorial then adds a frozen autoencoder
so the same UNet can work on a compressed representation instead.

Validation reports the noise-prediction MSE on each site's held-out split.

## Data

The same chest X-ray cohort and loading path as
[`xray_classification`](../../image_classification/xray_classification/): DICOM files fetched per
accession, read through the pinned `PydicomReader` chain in `app_files/transforms.py` (copied from
that tutorial) and resized to **256×256**. The cohort's lesion columns are ignored — a generative
model needs only the pixels.

```bash
make -C fl-tutorials download-xray-data
```

## Job type: `standard`, not `diffusion_model`

Careful here — **this directory is named `diffusion_model`, but the job type it uses is `standard`**,
not the job type of the same name. `JOB_TYPE=diffusion_model` refers to the platform's older
*two-stage* (autoencoder-then-diffusion) job, which is still registered but which none of these three
tutorials uses. All three are ordinary single-stage FedAvg jobs, so they all declare
`JOB_TYPE=standard` and pair with
[`fl-apps/nvflare/standard/`](../../../../fl-apps/nvflare/standard/). The required upload set is
`trainer.py`, `config.json`, `models.py`; `transforms.py` is uploaded alongside them as an extra app
file.

## How this differs from the latent tutorial

The denoising objective is identical — sample a timestep, add noise, predict it, score with MSE.
What working in pixel space removes:

- **`DiffusionInferer` replaces `LatentDiffusionInferer`.** It takes only a scheduler, and has no
  `autoencoder_model` argument.
- **No latent scale factor.** The latent tutorial has to encode a sample batch and normalise by its
  standard deviation before it can train; there is nothing to normalise here.
- **Noise is sampled at image shape.** `[B, image_channels, *spatial_shape]` — see
  `image_noise_shape()` in `app_files/trainer.py`. The latent tutorial instead pads the latent grid so
  the UNet can downsample it cleanly.
- **`in_channels`/`out_channels` are the image channel count** (1 for these single-channel
  radiographs), not an autoencoder's `latent_channels` (3 in the latent tutorial). This is the config
  difference most easily got wrong when adapting one tutorial into the other.

## Cost, and sizing `net_config`

Denoising at full resolution is heavier than denoising a compressed latent — that cost is the whole
reason latent diffusion exists. In 2-D it is very manageable, which is what makes this a good tutorial
to run first, but it is still the larger of the two networks here (~21M parameters against the latent
job's ~27M working on a 16× smaller grid).

The shipped ladder is `[64, 128, 192, 256]` with two res-blocks per level and self-attention **only at
the deepest level**, where a 256×256 image has been downsampled to 32×32. Enabling attention at
shallower levels is the fastest way to run out of memory. Channel counts must be multiples of 32
(`GroupNorm(32)`).

One hard constraint: **`spatial_shape` must be divisible by `2 ** (len(channels) - 1)`** (8 for the
shipped four-level ladder), or the UNet's skip connections will not line up. The transform chain
resizes every image to exactly `spatial_shape`, so this is a config invariant — `fl-tutorials/tests/`
pins it.

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

The same grid is produced by a `LOCAL_DEV` run without the flag; the flag is what makes it survive
as a file rather than a logged tensor shape.

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
called exactly `LOCAL_ROUNDS` and rejects the job otherwise.

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
`app/custom/`). This is the fastest way to verify the job wiring.

### Local simulation (requires a GPU + the dataset)

```bash
make -C ../../.. download-xray-data      # once
make run                                 # delegates to `make sim`
```

Or via the shared harness:

```bash
make -C fl-tutorials run-tutorial TUTORIAL=diffusion_model
```

Knobs: `NUM_ROUNDS` (default 1), `N_CLIENTS` (default 2) — override per invocation
(`make run NUM_ROUNDS=2`) or in `.env.app`. In simulation the two sites train on different halves of
the dev dataset; in production each trust's cohort is already its own.

### Clean

```bash
make clean
```
