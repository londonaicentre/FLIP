<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

# Image generation tutorials (FLARE)

Three tutorials that train generative models on FLIP, each an ordinary federated-averaging job (job
type `standard`). Two of them are the two halves of one latent diffusion model for brain MRI; the
third generates chest X-rays on its own.

| Tutorial | What it trains | Data | Needs before it runs |
| --- | --- | --- | --- |
| [`brain_mri_ldm_stage1_autoencoder`](brain_mri_ldm_stage1_autoencoder/) | an autoencoder that compresses 3-D brain MRI into a small latent volume | brain MRI (MSD Task01), four MR sequences | nothing |
| [`brain_mri_ldm_stage2_diffusion`](brain_mri_ldm_stage2_diffusion/) | a latent diffusion model that generates 3-D brain MRI, told which MR sequence to make | brain MRI | an autoencoder: the published one, or your own from stage 1 |
| [`xray_diffusion`](xray_diffusion/) | a diffusion model that generates 2-D chest X-rays directly in pixels | chest X-rays (Ark+) | nothing |

## Brain MRI: one model, two jobs

A latent diffusion model is an autoencoder plus a diffusion model that works in the autoencoder's
latent space. On FLIP they are trained as two jobs, one after the other: the autoencoder tutorial
first, then the latent diffusion tutorial with that autoencoder frozen. The
[autoencoder tutorial](brain_mri_ldm_stage1_autoencoder/README.md#how-the-two-jobs-fit-together)
shows how they fit together.

**To generate brain MRI you can skip the first job.** We trained an autoencoder with the autoencoder
tutorial and published it on Hugging Face
([`aicentreflip/tutorials-brain-mri-latent-diffusion`](https://huggingface.co/aicentreflip/tutorials-brain-mri-latent-diffusion)).
The latent diffusion tutorial starts from it: see
[Getting the autoencoder](brain_mri_ldm_stage2_diffusion/README.md#getting-the-autoencoder-do-this-first).

## Chest X-rays

[`xray_diffusion`](xray_diffusion/) stands alone, and is the simplest of the three to read: 2-D
images, no autoencoder, and the diffusion model works on the pixels directly.

## Debugging

**Sample images.** A generative model's losses say little about its images, so in a local run the
tutorials can save them: reconstructions (autoencoder) or generated samples (both diffusion
tutorials). They are written only when all three hold:

| Condition | Set by |
| --- | --- |
| `"SAVE_DEBUG_SAMPLES": true` | you, in the tutorial's `config.json` (ships `false`) |
| `LOCAL_DEV=true` | the simulator (every trust sets `false`) |
| `DEBUG_SAMPLES_DIR` set | `make run` / `make sim` only |

On a trust the last two never hold, so nothing is written whatever `config.json` says, and the
plotting code (`dev/plot_utils.py`) is not even there: it is staged into simulator runs only, never
uploaded. Images land in `fl-tutorials/data/debug_samples/<tutorial>/` (gitignored); nothing sends
them anywhere.

**Loss curves.** After or during a local run, from the tutorial's directory:

```bash
DEBUG_SAMPLES_DIR=../../../data/debug_samples/<tutorial> python ../dev/plot_utils.py
```

It reads the simulator's logs and writes `losses_<site>.png` beside the samples.
