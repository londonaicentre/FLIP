# Autoencoder — FLIP tutorial

This tutorial trains a KL-regularised autoencoder (MONAI `AutoencoderKL`) and a patch discriminator
on 3-D brain MRI, using the **FLARE Client API** (`nvflare.client`), with the job defined in Python
via `FlipFedAvgRecipe`. The trained autoencoder is the first half of a latent diffusion model: the
[latent diffusion tutorial](../latent_diffusion_model/README.md) loads it and keeps it frozen. The
[guide](../README.md) explains how the three image-synthesis tutorials fit together.

## Example outputs from this tutorial

![Autoencoder reconstructions: input vs reconstruction in sagittal, coronal and axial views](assets/example_reconstructions.png)

Brain-MRI studies from one site, each shown as input vs reconstruction in the sagittal, coronal and
axial planes.

## Compatible job type

This tutorial is designed for `JOB_TYPE=standard`
([`fl-apps/nvflare/standard/`](../../../../fl-apps/nvflare/standard/)): an ordinary single-stage
FedAvg job, after which every site evaluates the final global model.

## Data requirements

The **brain MRI** cohort: MSD Task01_BrainTumour, the Medical Segmentation Decathlon's cut of BraTS
(see [`fl-tutorials/datasets/brain_mri/`](../../../datasets/brain_mri/)). On the platform,
`query.sql` selects every brain MR study, one row per accession. Each study holds **four
co-registered MR sequences** (FLAIR, T1w, T1Gd and T2w), each a separate single-channel NIfTI
volume. The trainer reads NIfTI only, so create the project with **DICOM to NIfTI** on.

**Every sequence is a training sample.** One autoencoder learns to compress all four, which lets the
latent diffusion tutorial train a single model that is told which sequence to generate. Each file's
sequence is read from its name (`app_files/modality.py`), and `MODALITIES` in `config.json` selects
which sequences train:

```json
"MODALITIES": ["FLAIR", "T1w", "T1Gd", "T2w"]   // all four (shipped)
"MODALITIES": ["T1w"]                            // one sequence
```

Each volume is resampled to **2 mm voxels** (`voxel_spacing`), centre-cropped or padded to a
**96 × 112 × 80** grid (`spatial_shape`), then z-scored; training adds a small random affine. The
split into training and validation (`VAL_SPLIT`) is by accession, so the four sequences of one study
always land on the same side. The cohort's tumour labels are not used: training is unsupervised.

For a local run, download the data from the repository root:

```bash
make -C fl-tutorials download-brain-mri-msd-raw            # 7.6 GB MSD tar, once
make -C fl-tutorials download-brain-mri-data NUM_CASES=20  # -> fl-tutorials/data/brain_mri/
```

In the simulator the two sites train on different halves of these studies (the trainer splits on
the FLARE site name); on the platform each trust's cohort is already its own.

## The network

`app_files/models.py` builds two networks from `net_config` in `config.json`, trained and aggregated
together:

- **`AutoencoderKL`**: an encoder of residual blocks at three resolutions (64, 128 and 256
  channels, two blocks per level, no attention) that maps a 96 × 112 × 80 volume to a
  **3 × 24 × 28 × 20** latent (3 latent channels), and a mirror-image decoder that maps it back.
- **`PatchDiscriminator`** (4 layers, 32 base channels), which scores local patches as real or
  reconstructed. It is 3-D by default; `net_config.discriminator.spatial_dims: 2` makes it score 12
  randomly chosen axial slices of each volume instead.

The autoencoder submodule must stay named `autoencoder`: the latent diffusion tutorial loads this
run's `autoencoder.*` weights by that prefix, and a renamed submodule would load nothing.

## The training logic

Each `train` task runs `LOCAL_ROUNDS` epochs on the received model and sends back the weight
difference. Per batch:

1. **Generator step.** `W_RECONSTRUCTION`·L1 + `W_PERCEPTUAL`·perceptual + `W_KL`·KL +
   `W_GAN`·adversarial: L1 between input and reconstruction, the MedicalNet perceptual distance
   (below), the KL divergence of the latent from a unit Gaussian, and the discriminator's verdict.
2. **Discriminator step.** The discriminator learns to tell real volumes from the (detached)
   reconstructions, with a least-squares adversarial loss scaled by `W_GAN`.

Both steps run under mixed precision; the KL term is computed in float32, because squaring sigma
overflows float16. Gradients are accumulated over batches of `BATCH_SIZE` to an effective batch of 8
volumes per optimiser step, and both networks use AdamW.

The learning rate starts at `LR_START`. Add `LR_END` and `LR_DECAY_EPOCHS` to `config.json` to
decay it along half a cosine over the job's first `LR_DECAY_EPOCHS` epochs, counted across rounds,
then hold it at `LR_END`. The other two image-synthesis tutorials use the same rule.

Validation runs on the held-out split after every epoch and reports the L1 loss and **foreground
SSIM**: the skull-stripped background, about 80 % of the grid, scores close to 1 for any
reconstruction, so it is excluded. On the `evaluate` task the trainer scores the global model the
same way and reports SSIM as `val_acc` (and L1 as `val_loss`).

## The perceptual loss (`make weights`)

The perceptual loss compares input and reconstruction in the features of **MedicalNet ResNet-50**, a
3-D network pretrained on 23 medical datasets: Chen, Ma & Zheng, *Med3D: Transfer Learning for 3D
Medical Image Analysis*, [arXiv:1904.00625](https://arxiv.org/abs/1904.00625) (2019), with weights
released by Tencent under the MIT licence
([`TencentMedicalNet/MedicalNet-Resnet50`](https://huggingface.co/TencentMedicalNet/MedicalNet-Resnet50)).
Cite the paper if you publish results that use this loss.

An FL app never downloads at run time (FLIP#1206): the FL server and a trust host may have no
internet route, and a downloaded file bypasses the scanned upload path. So the weights ship as an
uploaded file, staged by:

```bash
make weights   # -> app_files/medicalnet_resnet50_23datasets-ff48a622.pth (gitignored)
```

`make export` and `make sim` run it for you. **Upload the file with the other app files.**
`app_files/medicalnet_perceptual.py` builds the network from MONAI's `ResNetFeatures` and loads that
file, checking its hash and refusing a partial load.

## Debugging

Sample images (tri-planar input vs reconstruction figures) need `"SAVE_DEBUG_SAMPLES": true` and a
local simulator run; loss curves come from `python ../dev/plot_utils.py`. Both are described in the
[guide's debugging section](../README.md#debugging).

## FLIP-specific values

`FLIP_PROJECT_ID` and `FLIP_QUERY` are read from environment variables (stubs in `.env.app`).
They are NOT passed as CLI flags because the SQL query contains spaces that don't survive
argparse whitespace-splitting. The trainer reads the query from `config_fed_client.json` at
runtime via `load_query()`. `.env.app` ships `FLIP_PROJECT_ID=dev`: LOCAL_DEV ignores the value,
but the `--project_id {project_id}` task arg needs a non-empty token to substitute.

## How to run

### Export (primary — no GPU needed)

Produces a complete FLARE job directory under `./fl_job/flip_fedavg/` including:
- `meta.json` with `custom_props.model_id` (dev UUID for local use)
- `app/config/config_fed_server.json` and `config_fed_client.json`
- `app/custom/` with the bundled `flip/` package and the app files staged alongside it

```bash
make export
```

or equivalently, after `make weights` (the **flip-utils** environment with the `full` extra the
`flare-fl-base` FL image installs, plus the `diffusion` group for the local plotting helper):

```bash
uv run --project ../../../../flip-utils --extra full --group diffusion python job.py --export --export-dir ./fl_job --n_clients 2 --num_rounds 1
```

> **Note:** `make run` (invoked by the tutorial runner `run-tutorial`/`run-all-tutorials`)
> delegates to `make sim`. Use `make export` (no GPU) or `make sim`/`make run` (GPU + data).

### Local simulation (requires a GPU + the dataset)

With the dataset downloaded (see *Data requirements*), run `make sim` here, or
`make -C fl-tutorials run-tutorial TUTORIAL=autoencoder` from the repository root. The simulator
runs one site at a time (a site peaks at about 20 GB of GPU memory) and writes its workspace, final
global model included, under `/tmp/nvflare/autoencoder/`.

### Overriding rounds and clients

`NUM_ROUNDS` (default `1`) and `N_CLIENTS` (default `2`) parameterise both `make export` and
`make sim`/`make run`, and propagate through the tutorial harness:

```bash
make sim NUM_ROUNDS=2
make export NUM_ROUNDS=2 N_CLIENTS=3
make -C fl-tutorials run-tutorial TUTORIAL=autoencoder NUM_ROUNDS=2
```

> **Local knob only.** `--num_rounds` governs local simulation and export. In production the FL API
> reads `GLOBAL_ROUNDS` from `config.json` at submit time and overrides whatever `job.py` baked into
> the exported config — deployed round counts come from `config.json`, never from these flags.

### On the platform

Run `make weights`, then upload through the FLIP UI, as a model with job type `standard`, every file
in `app_files/` (`trainer.py`, `config.json`, `models.py`, `transforms.py`, `modality.py`,
`medicalnet_perceptual.py`) **plus** the staged `medicalnet_resnet50_23datasets-ff48a622.pth`. Use
`query.sql` as the cohort query. The standalone FLARE submit path
(`make -C fl-services/nvflare submit`) is not wired, so the UI is the way in.
