# Autoencoder (VAE) — FLIP tutorial

This tutorial trains a **KL-regularised autoencoder plus a patch discriminator** on **3-D brain
MRI**, using the **NVFLARE Client API** (`nvflare.client`). The job is defined entirely in Python
via `FlipFedAvgRecipe` — no hand-written JSON configs required. The code is entirely based on `MONAI`
functions.

It is one of three tutorials that between them cover generative image synthesis on FLIP:

| Tutorial | Trains | Operates on | Needs an uploaded model? |
| --- | --- | --- | --- |
| **`autoencoder`** (this one) | `AutoencoderKL` + `PatchDiscriminator` | 3-D brain MRI | no |
| [`diffusion_model`](../diffusion_model/) | `DiffusionModelUNet` | 2-D chest X-rays, in pixel space | no |
| [`latent_diffusion_model`](../latent_diffusion_model/) | `DiffusionModelUNet` | latents of a frozen autoencoder, 3D brain MRI | **yes** — the autoencoder |

The two that pair up — this one and `latent_diffusion_model` — share the brain-MRI cohort, because
the second consumes the first's output. `diffusion_model` stays on 2D X-rays on purpose: it is the
one to read first, and pixel-space diffusion at full 3D resolution is exactly the cost that makes
the latent approach worth explaining.

Each is an **independent single-stage job**.

Validation reports L1 reconstruction loss and **foreground SSIM** on each site's held-out split.

## Data

The **brain MRI** cohort — MSD Task01_BrainTumour, the Medical Segmentation Decathlon's cut of BraTS
(see [`fl-tutorials/datasets/brain_mri/`](../../../datasets/brain_mri/)). Each study is **four
co-registered MR sequences**: FLAIR, T1w, T1Gd and T2w, arriving as four separate single-channel
NIfTI volumes.

```bash
make -C fl-tutorials download-brain-mri-msd-raw            # 7.6 GB MSD tar, once
make -C fl-tutorials download-brain-mri-data NUM_CASES=20  # -> data/brain_mri/
```

**Every sequence is a training sample.** The autoencoder is deliberately modality-agnostic: one
autoencoder learns to compress all four, which is what lets `latent_diffusion_model` downstream
train a single diffusion model that is *told* which sequence to generate. `MODALITIES` in
`config.json` is the switch:

```json
"MODALITIES": ["FLAIR", "T1w", "T1Gd", "T2w"]   // all four (shipped)
"MODALITIES": ["T1w"]                            // single-sequence
```

Each file's sequence is read from its **filename** (`app_files/modality.py`). The simulator layout
writes `input_<label>_<case>.nii.gz`, and on the platform dcm2niix names the file from
`ProtocolName`, which the converter sets to the same label — so one parse works in both places with
nothing to configure per site. The label is matched as a whole `_`-separated token, not a substring,
which is what keeps `T1w` from also claiming `T1Gd`.

Nothing in *this* tutorial reads the modality; it is attached to each sample for the diffusion
tutorial's benefit. The tumour labels in the cohort are ignored entirely — this is unsupervised.


## Compatible job type

These files are compatible with `JOB_TYPE=standard` in the base application
([`fl-apps/nvflare/standard/`](../../../../fl-apps/nvflare/standard/)) — an ordinary single-stage
FedAvg job with cross-site validation. The required upload set is `trainer.py`, `config.json`,
`models.py`; `transforms.py`, `modality.py`, `debug_samples.py`, `medicalnet_perceptual.py` and the
shipped perceptual backbone (`medicalnet_resnet10_23datasets-afa8055f.pth`, produced by
`make weights`) are uploaded alongside them as extra app
files.

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


## 3D or 2D discriminator 

The discriminator can be either 2D or 3D. The reason for a 2D discriminator in a 3D training is that **anisotropy** can make a 2D discriminator more useful for the training. This is gated by  `self.is_volumetric`, which is simply
`net_config.spatial_dims == 3`:

- **`slice_volume_for_2d(volume, indices)`** folds selected axial slices of a 3-D batch into a 2-D
  batch, `(B, C, H, W, D) -> (B*len(indices), C, H, W)`. 12 slices are drawn per step
  (`self.perceptual_slices`). **Both operands of a comparison reuse the same indices** — otherwise
  the loss compares unrelated anatomy.
- **`detect_axial_anisotropy(path)`** reports whether a volume is thick-slice. It is *not* called by
  the shipped loader, deliberately: the chain resamples to an isotropic 96³ grid, so the answer would
  always be no, and asking on the raw geometry instead (240×240×155, a ratio of 1.55) would log
  "found axial anisotropy" about data that is about to be made isotropic. It is there for a cohort
  that is genuinely thick-slice and resampled to a grid that keeps it that way.
  The perceptual loss no longer uses this path: MedicalNet takes the volume whole. Slicing it was
  a quiet source of dilution — a random axial slice of a skull-stripped head is often mostly air,
  and scores near zero whatever the model did.

**`net_config.spatial_dims` is the top-level key, and the one every network is built from.**
`stage_1` carries a copy that `models.py` does not read; a parity test fails if the two disagree,
because a config that sets one and not the other looks coherent while the trainer and the network it
is training disagree about their own dimensionality.

## Note on the perceptual loss (`make weights`)

The perceptual loss is **MedicalNet ResNet-10**, a 3D network pretrained on 23 medical datasets. It
needs its weights, and **an FL app must never download at run time**: on a
platform-managed estate the FL server has no internet route, nor does a trust host behind an NHS
firewall, so the job would hang; and a run-time fetch bypasses the scanned upload path — the file a
Trust can inspect before training would not be the file that runs. So the backbone travels *with*
the app:

```bash
make weights   # → app_files/medicalnet_resnet10_23datasets-afa8055f.pth  (gitignored; `make sim` and `make export` run this for you)
```

That stages the checkpoint from the shared download
(`make -C fl-tutorials download-weights ARCH=medicalnet_resnet10`, fetched once against a pinned
sha256) into `app_files/`. It is 57 MB. **Upload it with the other app files** — it is a `.pth`, so
the platform's picklescan gates it like any checkpoint.

**Why this tutorial does not call MONAI's `PerceptualLoss`.** MONAI exposes the same network as
`PerceptualLoss(spatial_dims=3, network_type="medicalnet_resnet10_23datasets")`, but that
constructor reaches the network twice over: `torch.hub.load` fetches the
`Project-MONAI/perceptual-models` **repository**, whose `download_model` then pulls the weights from
Hugging Face. Staging a cache the way a single checkpoint is staged does not help, because what
torch.hub needs cached is a whole *repository directory*, and the upload path and job bundler are
flat. So `app_files/medicalnet_perceptual.py` builds the identical architecture from MONAI's own
`ResNetFeatures` — which takes an architecture *name*, never a URL — and loads the staged file. The
published state dict lands in it with **zero missing and zero unexpected keys**, asserted at load
time: a partial load under `strict=False` would leave a randomly-initialised critic still reporting
plausible, smoothly-falling numbers.

Note that MedicalNet's raw values are 5–9× smaller than the 2-D LPIPS backbone, so adjust `w_perceptual_loss` according to the backbone. 

### A note on mixed precision

The training step runs under `autocast`, and the KL divergence is computed in **float32 regardless**
(`KLDivergenceLoss`). This is not defensive style — it is a fix for a real crash. The expression
squares sigma, fp16 tops out at 65504, and early in training sigma genuinely reaches ~2×10⁴ on this
cohort within about 15 steps. Squaring that overflows to `inf`, and `z_mu**2 + inf - log(inf) - 1` is
`inf - inf`, i.e. **NaN** — which reaches the weights and surfaces one step later as
`RuntimeError: NaN in autoencoder reconstruction during train`, pointing at the forward pass rather
than at the loss that poisoned it.

## Seeing what it generates (`SAVE_DEBUG_SAMPLES`)

Loss curves are a poor judge of a generative model, and an autoencoder is the clearest case: L1 falls just as convincingly while the network learns to emit a well-centred blur. Set

```json
"SAVE_DEBUG_SAMPLES": true,
"DEBUG_SAMPLES_MAX": 8
```

in `config.json`. For a **local simulator run** the images land beside the datasets:

```
fl-tutorials/data/debug_samples/autoencoder/
```

which is stable across runs and already gitignored. On a **trust** they go to
`app_files/debug_samples/` inside that client's own job workspace instead — per client, per run,
and never outside the job the trust agreed to run.

The difference is one environment variable, `DEBUG_SAMPLES_DIR`, which the tutorial Makefile sets
for `make sim` and which does not exist on a trust. It is deliberately *not* a `config.json` key:
that file is uploaded with the app and read on the trust, so a path in it would follow the job into
production and ask a client to write patient-derived images wherever the config said.

This needs `matplotlib`, which is in `app_files/requirements.txt` and in the base image; the module
imports it lazily and forces the `Agg` backend, since an FL client has no display.

## Base-image dependency (torchvision)

This tutorial needs `torchvision` at runtime for the 2-D discriminator path; it must be built
against the **same torch** as the
`flare-fl-base` image (pinned `torch>=2.11`, cu128, in
[`flip-utils/pyproject.toml`](../../../../flip-utils/pyproject.toml)). A base image whose
`torchvision` predates that pin fails at runtime with
`RuntimeError: operator torchvision::nms does not exist`. (The `app_files/requirements.txt` lists
`torchvision` too, but that file is a dependency *spec* — the runtime deps come from the base image,
not from installing it per job.) The perceptual network's weights are **not** downloaded at run
time — see the next section.

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
make -C ../../.. download-brain-mri-msd-raw   # 7.6 GB, once
make -C ../../.. download-brain-mri-data      # -> data/brain_mri/
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
