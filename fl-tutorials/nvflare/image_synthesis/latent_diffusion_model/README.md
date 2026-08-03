# Latent diffusion model

> **Note:** this is the legacy Executor-based tutorial. The NVFLARE **Client API**
> sibling is [`latent_diffusion_model_client_api`](../latent_diffusion_model_client_api/README.md).

This code allows to train a two-stage latent diffusion model. The main model is made up of several stages: a variational autoencoder and a diffusion model.
The code is entirely based on `MONAI` functions.

Both the trainer and validator contain training loops for both stages and, depending on the task name, a stage is trained or another.

The validation provides L1 loss and SSIM values for the stage 1 and L1 loss for the diffusion model. If `LOCAL_DEV=True`, plots are provided as well for both stages, saved
on the client directory.

## Compatible job type

These files are compatible with `JOB_TYPE=diffusion_model` in the base application.

## Base-image dependency (torchvision)

Unlike the other tutorials, this one needs `torchvision` at runtime: the validator's perceptual
loss uses `lpips`, which calls `torchvision.ops` operators (e.g. `nms`). Those must be built against
the **same torch** as the `flare-fl-base` image (pinned `torch>=2.11`, cu128, in
[`flip-utils/pyproject.toml`](../../../flip-utils/pyproject.toml)). A base image whose `torchvision`
predates that pin fails at runtime with `RuntimeError: operator torchvision::nms does not exist`.

This is addressed by **PR #624** (it re-locks the FL image dependencies). If your
`flare-fl-base:stag` predates #624, run against a fixed image tag instead:

```bash
make -C fl-tutorials run-tutorial TUTORIAL=latent_diffusion_model FL_BASE_IMAGE_TAG=<fixed-tag>
```

(The `app_files/requirements.txt` lists `torchvision` too, but that file is a dependency *spec* — the
runtime deps come from the base image, not from installing it per job.)