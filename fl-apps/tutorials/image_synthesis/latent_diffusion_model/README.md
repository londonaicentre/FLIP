# Latent diffusion model

This code allows to train a two-stage latent diffusion model. The main model is made up of several stages: a variational autoencoder and a diffusion model.
The code is entirely based on `MONAI` functions.

Both the trainer and validator contain training loops for both stages and, depending on the task name, a stage is trained or another.

The validation provides L1 loss and SSIM values for the stage 1 and L1 loss for the diffusion model. If `LOCAL_DEV=True`, plots are provided as well for both stages, saved
on the client directory.

## Compatible job type

These files are compatible with `JOB_TYPE=diffusion_model` in the base application.