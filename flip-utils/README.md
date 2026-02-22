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

# FLIP-FL-base-FLARE

<p align="left">
<img src="docs/images/flip-flare-logo.png" height="200" alt='flip-flare-logo' />
</p>

This repository contains the federated learning base code to create FLARE deployments with FLIP, as well as tutorials of apps using FLIP with FLARE. It includes the FL services Docker images (server, clients, FL API) and example applications for different job types (standard federated training, distributed evaluation, diffusion model training, and custom federated optimization).

## Quick Start

### Prerequisites

- Docker and Docker Compose
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- AWS CLI configured (for downloading test data)

### 1. Provision an FL Network

Before running anything, you need to provision a federated learning network. This generates the required certificates, keys, and configuration files:

```bash
make nvflare-provision NET_NUMBER=1
```

This creates:

- Network-specific compose file: `deploy/compose-net-1.yml`
- Service secrets in `workspace/net-1/services/` (gitignored)

You can provision multiple networks with different ports:

```bash
make nvflare-provision NET_NUMBER=2 FL_PORT=8004 ADMIN_PORT=8005
```

**Warning**: Provisioned files contain cryptographic signatures. Any modification will cause errors. Always re-run provisioning if changes are needed.

### 2. Build Docker Images

```bash
make build NET_NUMBER=1
```

### 3. Start the FL Network

```bash
make up NET_NUMBER=1
```

This starts:

- `fl-server-net-1`: Aggregation server
- `fl-client-1-net-1`, `fl-client-2-net-1`: Training clients
- `flip-fl-api-net-1`: FastAPI admin interface

To stop the network:

```bash
make down NET_NUMBER=1
```

To clean up images and containers:

```bash
make clean NET_NUMBER=1
```

## Development Mode

DEV mode lets you test your FL applications locally before deploying to production.

### Configure Environment

Edit `.env.development`:

```bash
LOCAL_DEV=true
DEV_IMAGES_DIR=../data/accession-resources    # Path to your images
DEV_DATAFRAME=../data/sample_get_dataframe.csv  # Path to your dataframe
JOB_TYPE=standard
```

### Add Your Application Files

Place your files in `src/<JOB_TYPE>/app/custom/`:

- `trainer.py` - Training logic (FLIP_TRAINER executor)
- `validator.py` - Validation logic (FLIP_VALIDATOR executor)
- `models.py` - Model definitions (`get_model` function)
- `config.json` - Hyperparameters (requires `LOCAL_ROUNDS` and `LEARNING_RATE`)
- `transforms.py` - Data transforms (optional)

### Run the Simulator

```bash
make run-container
```

This runs the NVFLARE simulator in Docker with 2 clients, mounting your app folder for live changes.

## Testing

### Download Test Data

Download x-ray classification test data (requires AWS S3 access):

```bash
make download-xrays-data
```

Download spleen segmentation test data (requires AWS S3 access):

```bash
make download-spleen-data
```

Download model checkpoints for evaluation tests:

```bash
make download-checkpoints
```

### Run Integration Tests

Test different job types with the spleen dataset:

```bash
# Standard federated training (classification task)
make test-xrays-standard

# Standard federated training (segmentation task)
make test-spleen-standard

# Model evaluation pipeline (requires model checkpoint file)
make test-spleen-evaluation

# Diffusion model training
make test-spleen-diffusion

# Run all integration tests
make test
```

### Run Unit Tests

```bash
make unit-test
```

### Manage Test Applications

Copy the spleen test app to your dev folder:

```bash
make copy-spleen-app
```

Save your changes back to the test folder:

```bash
make save-spleen-app
```

## Project Structure

```text
├── src/                    # FL application types
│   ├── standard/           # Standard FedAvg training
│   ├── evaluation/         # Distributed model evaluation
│   ├── diffusion_model/    # Two-stage VAE + diffusion training
│   └── fed_opt/            # Custom federated optimization
├── fl_services/            # NVFLARE service definitions
│   ├── fl-base/            # Base Docker image
│   ├── fl-api-base/        # FastAPI admin service
│   ├── fl-client/          # Base FL client service
│   └── fl-server/          # Base FL server service
├── deploy/                 # Docker compose files and templates
├── workspace/              # Provisioned secrets (gitignored)
├── tests/                  # Integration test applications
|  ├── examples/            # Example applications for integration testing
|  └── unit/                # Unit tests
└── .env.development        # Local environment configuration
```

## Job Types

Set via `JOB_TYPE` environment variable:

| Type | Description |
| ------ | ------------- |
| `standard` | Federated training with FedAvg aggregation (default) |
| `evaluation` | Distributed model evaluation without training |
| `diffusion_model` | Two-stage training (VAE encoder + diffusion) |
| `fed_opt` | Custom federated optimization |

## NVFLARE App Structure

An NVFLARE app requires this structure:

```text
app/
├── config/
│   ├── config_fed_server.json
│   └── config_fed_client.json
└── custom/
    ├── trainer.py
    ├── validator.py
    ├── models.py
    └── config.json
```

For different configurations per client/server, use multiple app folders with a `meta.json` containing a `deploy_map`. See [NVFLARE documentation](https://nvflare.readthedocs.io/en/2.6/real_world_fl/job.html).

## Application and tutorials

Applications that will run on FLIP will take files from the `app` of choice (contained in both the `custom` and `config` folders described above), and files that are uploaded by the user to the UI. These files are customisable by the user, and examples compatible with different types of apps will be available in `tutorials`. 

![image.png](./assets/fl_app_structure.png)

These are the following app / tutorial compatibilities:

| App | Tutorial | 
|-----|----------|
|`standard`|`image_segmentation/3d_spleen_segmentation`|
|`diffusion_model`|`image_synthesis/latent_diffusion_model`|
|`fed_opt`|`image_segmentation/3d_spleen_segmentation`|
|`evaluation`|`image_evaluation/3d_spleen_segmentation`|
|`standard`|`image_classification/xray_classification`|

## User Application Requirements

The standard application requires:

| File | Description |
|------|-------------|
| `trainer.py` | Training logic with `FLIP_TRAINER` class inheriting from Executor |
| `validator.py` | Validation logic with `FLIP_VALIDATOR` class inheriting from Executor |
| `models.py` | Model definitions with `get_model()` function |
| `config.json` | Must include `LOCAL_ROUNDS` and `LEARNING_RATE` |

## Production Testing via GitHub Actions

Pull requests automatically push to a dev S3 bucket for testing:

```text
s3://flipdev/base-application-dev/pull-requests/<PR_NUMBER>/src/
```

To test on the FLIP platform, update `FL_APP_BASE_BUCKET` in the [flip repo environment variables](https://github.com/londonaicentre/FLIP/blob/main/.env.development) to point to your PR's bucket.

## S3 Bucket Mounting (Optional)

For automatic sync between local development and S3:

1. Install [s3fs](https://github.com/s3fs-fuse/s3fs-fuse)

2. Configure credentials:

   ```bash
   echo ACCESS_KEY_ID:SECRET_ACCESS_KEY > ~/.passwd-s3fs
   chmod 600 ~/.passwd-s3fs
   ```

3. Mount the bucket:

   ```bash
   s3fs flip:/base-application-dev/src/standard/app/ ./app/ -o passwd_file=${HOME}/.passwd-s3fs
   ```

For automatic mounting on boot, add to `/etc/fstab`:

```bash
flip <PATH_TO_APP>/app fuse.s3fs _netdev,allow_other 0 0
```

Test with `mount -a` before relying on it.

## CI/CD

These workflows use GitHub OIDC to securely authenticate to AWS (no long-lived AWS keys required). They use an IAM role with a policy that allows S3 operations.

- **PR to any branch**: Pushes to dev S3 bucket for testing on AWS dev account:
  - (dev) `s3://flipdev/base-application-dev/pull-requests/<PR_NUMBER>/src/`
- **Merge to develop**: Syncs `src/` to S3 buckets on AWS dev and staging accounts:
  - (dev) `s3://flipdev/base-application-dev/src/`
  - (staging) `s3://flipstag/base-application/src/`
- **Merge to main**: Syncs `src/` to S3 bucket in AWS prod account:
  - (prod) `s3://flipprod/base-application/src/`

> **Warning**: Never manually sync to the production bucket.

## Makefile Reference

### Network Management

| Command | Description |
| --------- | ------------- |
| `make nvflare-provision NET_NUMBER=X` | Provision FL network X |
| `make build NET_NUMBER=X` | Build Docker images for network X |
| `make up NET_NUMBER=X` | Start FL network X |
| `make down NET_NUMBER=X` | Stop FL network X |
| `make clean NET_NUMBER=X` | Remove containers and images |

### Development

| Command | Description |
| --------- | ------------- |
| `make run-container` | Run NVFLARE simulator in Docker |

### Testing Commands

| Command | Description |
| --------- | ------------- |
| `make unit-test` | Run pytest unit tests |
| `make test-spleen-standard` | Test standard job with spleen data |
| `make test-spleen-evaluation` | Test evaluation job with spleen data |
| `make test-spleen-diffusion` | Test diffusion model with spleen data |
| `make test` | Run all integration tests |

### Data Management

| Command | Description |
| --------- | ------------- |
| `make download-spleen-data` | Download spleen test images from S3 |
| `make download-checkpoints` | Download model checkpoints from S3 |
| `make copy-spleen-app` | Copy test app to dev folder |
| `make save-spleen-app` | Save dev changes to test folder |
| `make pull-spleen-app` | Pull latest app from tutorials repo |
