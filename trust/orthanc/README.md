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

# Orthanc (mock PACS)

> We use Orthanc as a mock PACS server to store and serve DICOM files for testing purposes. Orthanc is an open-source, lightweight DICOM server that provides a RESTful API for managing medical images. Read more about Orthanc in the [Orthanc documentation](https://www.orthanc-server.com/).

Orthanc username and password are set by `ORTHANC_USERNAME` and `ORTHANC_PASSWORD` in the per-trust kit file `trust/.env.<CODE>.<env>` — see the **Trust-local credentials** section of [.env.GSTT.development.example](../.env.GSTT.development.example). These are trust-local secrets; the hub never sees them.

You'll need to populate Orthanc with DICOM files in order to test FLIP locally. We have prepared mock DICOM data for each of the 2 dev trusts (GSTT and KCH) as Orthanc storage volumes, published to the public Hugging Face dataset [`aicentreflip/trust-data`](https://huggingface.co/datasets/aicentreflip/trust-data). In order to set up the storage locally, these data volumes need to be downloaded/extracted. They are fetched anonymously over HTTPS — no AWS CLI or credentials required. This is handled automatically when bringing up the trust containers via `make up` / `make up-trusts` (from the repository root) or `make -C trust up-trust KIT=GSTT` / `make -C trust up-trust KIT=KCH` for a single trust, and similarly they will be updated locally when the desired version changes (note for devs: this is controlled by the `.data_version` file in this directory).

```sh
make update-orthanc-data           # both trusts (default)
make update-orthanc-data TRUST=1   # Trust_1 only
make update-orthanc-data TRUST=2   # Trust_2 only
```
