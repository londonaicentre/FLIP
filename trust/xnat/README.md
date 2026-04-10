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

# XNAT

XNAT is the medical imaging archive used by each FLIP trust site. It stores DICOM data imported from the trust's PACS (Orthanc) and makes it available to federated learning pipelines via the Imaging API. This directory contains the Docker configuration for running XNAT locally and on EC2, along with the plugins and setup scripts needed to integrate it with the rest of the FLIP Trust Services layer.

## Docker Swarm

XNAT is deployed using Docker Swarm (both locally and on EC2). This is because Swarm provides overlay networking, resource constraints, and restart policies needed for XNAT services.

**Prerequisites:**
- Docker Swarm must be initialized before running XNAT: `docker swarm init`
- Overlay networks are created automatically by Make targets

**How it works:**
- `make up` / `make down` use `docker stack deploy` / `docker stack rm` under the hood
- The stack definition uses layered compose files, selected by the `PROD` variable:

  | Environment | Files |
  | --- | --- |
  | Development (default) | `docker-compose-stack.yml` + `docker-compose-stack.development.yml` |
  | Staging / Production | `docker-compose-stack.yml` + `docker-compose-stack.production.yml` |

  The base file defines the three services (`xnat-web`, `xnat-db`, `xnat-nginx`). The development overlay adds host bind-mounts for hot-reload and resource limits sized for dev machines. The production overlay mounts persistent data volumes under `/opt/flip/xnat/`.
- Two XNAT instances are deployed as separate Swarm stacks (`xnat1`, `xnat2`), one per trust

## Setup

Note you need Orthanc running in order to startup XNAT and configure it properly (see [orthanc](../orthanc/)).

### Build XNAT Docker images for local development

Build the XNAT Docker images locally (requires AWS CLI access for S3 artifacts):

```sh
make build
```

This downloads the XNAT WAR and plugins from S3, then builds all three images (`xnat-web`, `xnat-db`, `xnat-nginx`) tagged as `${DOCKER_REGISTRY}xnat-<service>:dev`.

### Run XNAT

After building, run with the `dev` tag:

```sh
DOCKER_TAG=dev make up
```

`make up` will create the required data directories, deploy the XNAT stack via Docker Swarm, and automatically configure both XNAT instances (service account, admin password, SCP receiver, PACS registration, dcm2niix command).

In development (when `PROD` is not set), `make up` also mounts the local `xnat/plugins` and `xnat/config` directories into the container for hot-reload. In production, these are baked into the Docker image.

> **Important — XNAT data volumes must be bind-mounted.**
> XNAT's container service plugin executes Docker commands on the host (via the mounted Docker socket). Those host-spawned containers need access to XNAT data through host paths, so the data directories (`archive`, `build`, `cache`, `tomcat_logs`) must be bind-mounted rather than using named volumes. The `/${XNAT_PORT}` prefix isolates data directories per XNAT instance. See `docker-compose-stack.development.yml` for the full volume configuration.

If successful, you will be able to log in to XNAT with the service account credentials (specified in the root `.env` file) and see the registered PACS in the DICOM Query-Retrieve plugin.

## Plugins

Plugins and the XNAT WAR are stored in S3 at `s3://<FLIP_ARTIFACTS_BUCKET_NAME>/xnat/plugins/` and `s3://<FLIP_ARTIFACTS_BUCKET_NAME>/xnat/xnat-web-<version>.war`. The CI workflows download them and bake them into the Docker image during the build.

### Plugin compatibility

Make sure to always use the correct version of the plugins that are compatible with the XNAT version specified. Check the plugin's compatibility matrix for more information (for example, see [DQR Plugin Compatibility Matrix](https://wiki.xnat.org/xnat-tools/dqr-plugin-compatibility-matrix)).

The following table lists the compatible versions of the plugins for the XNAT version `1.9.3` used in this environment.

| Plugin                          | Version for XNAT 1.9.3  |
| ------------------------------- | ----------------------- |
| Container Service Plugin        | 3.7.3                   |
| Batch Launch Plugin             | 0.9.0                   |
| DICOM Query-Retrieve Plugin     | 2.2.0                   |
| OHIF Viewer Plugin              | 3.7.1                   |

### Adding or updating a plugin

1. Upload the new `.jar` file to `s3://<FLIP_ARTIFACTS_BUCKET_NAME>/xnat/plugins/`.
2. Update the plugin compatibility table above.
3. Trigger the CI workflows (push or `gh workflow run`) to rebuild the image with the new plugin.

## Importing data

Use the Imaging API to import data from Orthanc to XNAT.

This can also be done manually from XNAT:

* At the top of the homepage, select the **Browse > All Projects** tab.
* Open your project.
* Click on **Import from PACS** on the right-hand sidebar.
* Select **Orthanc** as your **Source PACS** (the selected SCP Receiver should default to **XNAT:8104**).
* [Query PACS](https://wiki.xnat.org/xnat-tools/using-dqr-searching-the-pacs-and-importing-data#UsingDQR:SearchingthePACSandImportingData-Querying/SearchingforImageSessions).

## DICOM Anonymization

XNAT includes a built-in anonymization engine that processes incoming DICOM data to remove Protected Health Information (PHI) from DICOM headers. FLIP replaces the default XNAT anonymization script with a comprehensive site-wide script (`anon_script.das`) that removes patient identifiers, institutional identifiers, physician/operator identifiers, and pseudonymizes UIDs.

The anonymization script is configured automatically during XNAT initialization via `configure-xnat.sh`. It is applied to all incoming DICOM data when the SCP receiver has `anonymizationEnabled` set to `true`.

## DICOM to NIfTI Conversion

XNAT uses two mechanisms for dcm2niix conversion:

- **Command** (Container Service): The dcm2niix command is registered and enabled **site-wide** during initial XNAT setup (`configure-dcm2niix.sh`). This makes it available for manual use from the XNAT UI in any project.
- **Event Subscription** (Event Service): Per-project event subscriptions auto-trigger dcm2niix when DICOM scans are uploaded. These are created by the **Imaging API** during FLIP project creation, controlled by the `dicom_to_nifti` project setting.

The setup script (`configure-dcm2niix.sh`) intentionally does **not** create a site-wide event subscription. This ensures dcm2niix only auto-triggers for projects that have opted in via the `dicom_to_nifti` flag.

## Resource limits

XNAT's container service plugin can launch many containers simultaneously (e.g. multiple dcm2niix conversions during a large import). Without resource limits, this can exhaust host CPU and memory, causing XNAT itself to hang. Docker Swarm's deploy resource constraints (`reservations` and `limits`) prevent this by capping what each service can consume.

The development overlay (`docker-compose-stack.development.yml`) sets these constraints for `xnat-web` and `xnat-db`. EC2 deployments should also set limits, but the current instance type may be too small for the development values — adjust them to match the available resources on your target instance.

## Troubleshooting

All issues below have been resolved and are documented here for reference.

- **"Error: Cannot import" when importing studies from PACS** — The DICOM SCP Receiver was missing the correct identifier. Fix: set the identifier to `dqrObjectIdentifier` and enable custom processing under **Administer > Site Administration > DICOM SCP Receivers > XNAT**. Now handled automatically by `make up`.

- **`DockerRequestException` on container launch** — Container service plugin 3.4.3 is incompatible with Docker 25+ ([known issue](https://groups.google.com/g/xnat_discussion/c/2kh3J-p_8bE), [3.5.0 release notes](https://bitbucket.org/xnatdev/container-service/src/master/CHANGELOG.md)). Fixed by upgrading the plugin to 3.6.2+ and XNAT to 1.9.3 (see [compatibility matrix](https://wiki.xnat.org/container-service/container-service-compatibility-matrix#ContainerServiceCompatibilityMatrix-ContainerServiceCompatibilitywithXNAT)). Batch Launch and DQR plugins were also updated.

- **Container can't find data** — Path translation between the XNAT container and host was not configured. Now set automatically during `make up`.

## Attribution

This directory contains Docker configuration derived from the XNAT docker-compose
project maintained by Washington University School of Medicine.

Original source: https://github.com/NrgXnat/xnat-docker-compose

Modifications were made to integrate with the FLIP Trust Services layer.
