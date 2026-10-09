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

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/source/assets/flip-logo-text-dark.png">
    <img src="docs/source/assets/flip-logo-text.png" height="200" alt="FLIP">
  </picture>
</p>

# Federated Learning Interoperability Platform

[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Documentation Status](https://readthedocs.org/projects/londonaicentreflip/badge/?version=latest)](https://londonaicentreflip.readthedocs.io/en/latest/)
[![Coverage](https://codecov.io/gh/londonaicentre/FLIP/branch/main/graph/badge.svg)](https://codecov.io/gh/londonaicentre/FLIP)

FLIP is an open-source platform for federated training and evaluation of medical-imaging AI models across healthcare
institutions. Models travel to the data held inside each institution; patient data remains within the institution's
security boundary.

The platform combines a Central Hub for project orchestration with independently operated Trust nodes. It supports
both [NVIDIA FLARE](https://nvflare.readthedocs.io/) and [Flower](https://flower.ai/docs/) as federated-learning
backends. FLIP is developed by the [London AI Centre](https://www.aicentre.co.uk/) with Guy's and St Thomas' NHS
Foundation Trust and King's College London.

For the platform architecture, workflows, deployment guides, and user documentation, start with the
[FLIP documentation](https://londonaicentreflip.readthedocs.io/en/latest/).

## Get started

| I want to... | Guide |
| --- | --- |
| Run FLIP on my machine (Central Hub + two example Trusts) | [Run FLIP locally for development](docs/source/deploy-flip/deploy-local-dev.rst) |
| Deploy the Central Hub on AWS | [Deploy the Central Hub](docs/source/deploy-flip/deploy-central-hub.rst) |
| Add a Trust to a FLIP network | [On premises](docs/source/deploy-flip/deploy-flip-node-on-prem.rst) · [In a TRE](docs/source/deploy-flip/deploy-flip-node-in-tre.rst) · [On Kubernetes](trust/deploy/helm/README.md) |
| Build or run a federated-learning application | [Working with FLIP apps](https://londonaicentreflip.readthedocs.io/en/latest/working-with-flip-apps.html) · [FL tutorials](fl-tutorials/README.md) |
| Contribute to FLIP | [CONTRIBUTING.md](CONTRIBUTING.md) |

## Repository layout

FLIP is maintained as one monorepo. Each major area owns its detailed setup and operational documentation.

| Directory | Responsibility |
| --- | --- |
| [`flip-api/`](flip-api/) | Central Hub FastAPI service, database, scheduling, and project lifecycle |
| [`flip-ui/`](flip-ui/) | Vue 3 web application |
| [`trust/`](trust/) | Trust gateway, data and imaging APIs, local OMOP/PACS/XNAT services, and the trust node's deployment shapes (Compose, Helm chart, on-prem Ansible play) |
| [`flip-utils/`](flip-utils/) | Shared, pip-installable `flip` Python library |
| [`fl-services/`](fl-services/) | NVFLARE and Flower network services, images, and provisioning |
| [`fl-apps/`](fl-apps/) | Backend-specific application templates bundled by the Central Hub |
| [`fl-tutorials/`](fl-tutorials/) | Worked federated-learning applications and local runners |
| [`map-apps/`](map-apps/) | MONAI Application Package (MAP) templates for packaging FLIP-trained models for clinical deployment |
| [`deploy/`](deploy/) | Central Hub Compose files and the AWS provider (Terraform) |
| [`docs/`](docs/) | Sphinx source published on ReadTheDocs |
| [`scripts/`](scripts/) | Repository-wide development and deployment helpers |

## Citing FLIP

If you use FLIP in your research, please cite
[our paper](https://arxiv.org/abs/2609.36001):

```bibtex
@misc{garciadias2026flip,
  title         = {Making Cross-Continental Federated Learning Repeatable with {FLIP}: a Multi-Application Study},
  author        = {Garcia-Dias, Rafael and Bagur, Alexandre Triay and Tangwiriyasakul, Chayanin and Fernandez, Virginia and
                   Esmaeili, Parhom and Ittichaiwong, Piyalitt and Li, Yang and Adams, Lawrence and Buncharoen, Wason and
                   Chapman, Martin and Chayanond, Benjamaporn and Chunrod, Sadthavud and Fongsri, Tanawat and
                   Gibson, Kass and Plungprasertkul, Supat and Tangpanithandee, Supawit and Veerakanjana, Kanyakorn and
                   Goh, Vicky and Antonelli, Michela and Zhang, Joe and Kespechara, Kongkiat and Ourselin, Sebastien and
                   Cardoso, M. Jorge},
  year          = {2026},
  eprint        = {2609.36001},
  archivePrefix = {arXiv},
  url           = {https://arxiv.org/abs/2609.36001}
}
```

## Contributing and support

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request; commits must
include a [DCO sign-off](https://developercertificate.org/). Use
[GitHub Issues](https://github.com/londonaicentre/FLIP/issues) for bugs, feature proposals, and documentation gaps.

For security concerns, follow [SECURITY.md](SECURITY.md) rather than opening a public issue.

FLIP is licensed under the [Apache License 2.0](LICENSE.md).
