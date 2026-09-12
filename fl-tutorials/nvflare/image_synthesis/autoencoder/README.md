# Autoencoder (VAE) — FLIP tutorial

This tutorial trains a **KL-regularised autoencoder plus a patch discriminator** on 2-D chest
X-rays, using the **NVFLARE Client API** (`nvflare.client`). The job is defined entirely in Python
via `FlipFedAvgRecipe` — no hand-written JSON configs required. The code is entirely based on `MONAI`
functions.

It is one of three tutorials that between them cover generative image synthesis on FLIP:

| Tutorial | Trains | Operates on | Needs an uploaded model? |
| --- | --- | --- | --- |
| **`autoencoder`** (this one) | `AutoencoderKL` + `PatchDiscriminator` | images | no |
| [`diffusion_model`](../diffusion_model/) | `DiffusionModelUNet` | images directly (pixel space) | no |
| [`latent_diffusion_model`](../latent_diffusion_model/) | `DiffusionModelUNet` | latents of a frozen autoencoder | **yes** — the autoencoder |

Each is an **independent single-stage job**. This tutorial and `latent_diffusion_model` together
make up what used to be a single two-stage latent-diffusion job; splitting them means an autoencoder
can be trained, scored and re-used on its own, and a diffusion model can be trained against an
autoencoder you already have.

Validation reports L1 reconstruction loss and SSIM on each site's held-out split.

## Data

The same chest X-ray cohort and loading path as
[`xray_classification`](../../image_classification/xray_classification/): DICOM files are fetched per
accession and read through the pinned `PydicomReader` chain in `app_files/transforms.py`, copied from
that tutorial. The cohort query returns its lesion columns, but nothing here reads them — a
generative model needs only the pixels.

The one deliberate difference from the classifier is size: images are resized to **256×256** rather
than 224, because a power of two divides cleanly through every downsampling level of the autoencoder
and the two diffusion models. `transforms.py` exports `SPATIAL_SHAPE`, and `config.json`'s
`spatial_shape` must equal it (pinned by `fl-tutorials/tests/`).

```bash
make -C fl-tutorials download-xray-data
```

## Compatible job type

These files are compatible with `JOB_TYPE=standard` in the base application
([`fl-apps/nvflare/standard/`](../../../../fl-apps/nvflare/standard/)) — an ordinary single-stage
FedAvg job with cross-site validation. The required upload set is `trainer.py`, `config.json`,
`models.py`; `transforms.py` is uploaded alongside them as an extra app file.

## Feeding the latent diffusion tutorial

A run of this tutorial produces the autoencoder that
[`latent_diffusion_model`](../latent_diffusion_model/) loads as its **frozen** encoder. That handoff
rests on one naming contract: the autoencoder lives on a submodule called `autoencoder` in both
networks, so its parameters are keyed `autoencoder.*` in both state dicts. **Do not rename that
attribute in `app_files/models.py`** — the checkpoint load on the other side is `strict=False`, so a
mismatch is silently tolerated and the diffusion model then trains against a randomly-initialised
encoder rather than yours.

The `net_config.stage_1` block must also stay identical between the two tutorials, for the same
reason. See `process_tools/extract_autoencoder.py` in that tutorial for the conversion step, which
takes this run's downloaded result and emits the autoencoder-only checkpoint to upload.

## Network size

The shipped config is deliberately modest (~5.9M parameters: a 3.1M autoencoder and a 2.8M
discriminator): three levels of `[64, 128, 128]` channels, so a 256×256 image compresses to a 64×64
latent with 3 channels. Channel counts must stay multiples of 32 — both MONAI networks normalise with
`GroupNorm(32)`.

## Porting this tutorial to 3-D

This tutorial began on 3-D CT volumes, and `app_files/trainer.py` still carries the two helpers that
part needed, because they are the pieces that are not obvious to re-derive:

- **`detect_axial_anisotropy(path)`** — whether a volume is thick-slice (much coarser through-plane
  than in-plane). On such data a 3-D perceptual loss compares voxels that are not comparable.
- **`slice_volume_for_2d(volume, indices)`** — folds selected axial slices of a 3-D batch into a 2-D
  batch, `(B, C, H, W, D) -> (B*len(indices), C, H, W)`, so a 2-D perceptual loss or 2-D
  discriminator can score a 3-D reconstruction. **Both operands of a comparison must be sliced with
  the same indices**, or the loss compares unrelated anatomy.
- **`AutoencoderTrainer.reset_perceptual_to_anisotropic()`** — swaps in the 2-D perceptual network
  (and raises its weight, which reports much smaller values) once anisotropy is known.

They are live code, not comments: everything is gated on `self.is_volumetric`, which is simply
`net_config.spatial_dims == 3`. Set that to 3 and the slicing paths switch on by themselves.

**Change `net_config.spatial_dims`, the top-level key — that is the one every network is built
from.** `stage_1` carries a copy of it, and `models.py` does not read that copy; a parity test now
fails if the two disagree, because a config that sets one and not the other looks coherent while the
trainer and the network it is training disagree about their own dimensionality.

A full port needs three more things, which the move to chest X-rays replaced:

1. **A volumetric transform chain** in `transforms.py` — `Orientationd`/`Spacingd` and a
   `ResizeWithPadOrCropd` to a 3-element `spatial_shape`, instead of the 2-D resize.
2. **A NIfTI loader** in `build_datalist` — request `ResourceType.NIFTI` and collect
   `input_*.nii.gz` instead of `*.dcm`. Call `detect_axial_anisotropy` on the first readable volume
   and then `reset_perceptual_to_anisotropic()`, as the original loader did.
3. **Config updates** — `spatial_dims: 3` and a 3-element `spatial_shape` (the parity tests check the
   two agree, and deliberately do not insist on 2-D). Keep the discriminator at `spatial_dims: 2` to
   score slices; a 3-D discriminator on thick-slice data performs poorly, which is what the
   anisotropy warning is about.

One test-suite consequence: the three `transforms.py` files are registered in `DICOM_APPS`
(`fl-tutorials/tests/tutorial_apps.py`) because they read 2-D DICOM. A chain moved to NIfTI must be
**removed** from that registry — its `Orientationd` puts it on the documented NIfTI path instead,
where the suite's `swap_ij` correction would be wrong.

The two diffusion tutorials need the same three changes but no extra helpers: their noise shapes and
latent geometry are already derived from `spatial_shape`, so they follow whatever dimensionality it
has.

Debug images (below) need no porting either — `save_grid` tiles a volume's axial mid-slice, so a 3-D
run still writes PNGs you can look at.

## Seeing what it generates (`SAVE_DEBUG_SAMPLES`)

Loss curves are a poor judge of a generative model, and an autoencoder is the clearest case: L1 falls just as convincingly while the network learns to emit a well-centred blur. Set

```json
"SAVE_DEBUG_SAMPLES": true,
"DEBUG_SAMPLES_MAX": 8
```

in `config.json` and the client writes PNGs to `app_files/debug_samples/` **inside its own job
workspace** — per client, per run, gitignored.

You get two kinds of grid, each with the inputs on the top row and the model's
reconstructions on the bottom:

| File | Written by | Shows |
|---|---|---|
| `reconstruction_..._step<N>.png` | each local epoch | the **local** model, on the same first validation batch every time — so you can watch one set of radiographs sharpen (or not) across rounds |
| `reconstruction_aggregated_...png` | the `validate` task | the **aggregated** model on this site's data |

Rows are normalised together, not per image. That is deliberate: scaling each image independently
would rescale a washed-out reconstruction back onto its input's range and hide exactly the intensity
drift the comparison exists to show.

Nothing about this puts an image on the wire. The files are written beside the running training
script and no code path reads them back, adds them to an `FLModel` or hands them to the metrics
writer — the Client API's `SummaryWriter` carries scalars only, so it could not take one anyway. An
image leaves the site only if a person deliberately copies it out, which at a real trust is a
disclosure decision like any other, not something the job can do by itself.

It ships **off**, and is meant for local runs. Left on at a trust it accumulates patient-derived
images on that trust's disk, round after round, with no retention policy attached.

## Rounds configuration

`app_files/config.json` carries `GLOBAL_ROUNDS` (federated rounds) and `LOCAL_ROUNDS` (local epochs
per round). These names are load-bearing: with a single local-rounds key, the FL API requires it to
be called exactly `LOCAL_ROUNDS` and rejects the job otherwise.

## Base-image dependency (torchvision)

This tutorial needs `torchvision` at runtime: the perceptual loss uses `lpips`, which calls
`torchvision.ops` operators (e.g. `nms`). Those must be built against the **same torch** as the
`flare-fl-base` image (pinned `torch>=2.11`, cu128, in
[`flip-utils/pyproject.toml`](../../../../flip-utils/pyproject.toml)). A base image whose
`torchvision` predates that pin fails at runtime with
`RuntimeError: operator torchvision::nms does not exist`. (The `app_files/requirements.txt` lists
`torchvision` too, but that file is a dependency *spec* — the runtime deps come from the base image,
not from installing it per job.) The first run also downloads the perceptual network's weights.

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
make -C fl-tutorials run-tutorial TUTORIAL=autoencoder
```

Knobs: `NUM_ROUNDS` (default 1), `N_CLIENTS` (default 2) — override per invocation
(`make run NUM_ROUNDS=2`) or in `.env.app`. In simulation the two sites train on different halves of
the dev dataset (the trainer splits on the NVFLARE site name); in production each trust's cohort is
already its own.

### Clean

```bash
make clean
```
