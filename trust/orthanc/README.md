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

Orthanc username and password are set by `ORTHANC_USERNAME` and `ORTHANC_PASSWORD` environment variables in the .env.development file at the root of this repository (see example in [.env.development.example](../../.env.development.example)).

You'll need to populate Orthanc with DICOM files in order to test FLIP locally. We have prepared mock DICOM data for each of the 2 dev trusts (Trust_1 and Trust_2) as Orthanc storage volumes on S3. In order to set up the storage locally, these data volumes need to be downloaded/extracted. This is handled automatically when bringing up the trust containers via `make up`, `make up-trusts`, `make up-trust-1`, or `make up-trust-2`, and similarly they will be updated locally when they are updated on S3 (note for devs: this is controlled by `.data_version` file in this directory).

```sh
make update-orthanc-data
```

<!-- TODO add instructions to generate mock data using the MSD dataset. -->

If you want to override the mock data, you can also drop your own DICOM files into the `orthanc-storage` and `orthanc-storage-2` directories — these are bind-mounted into the Orthanc containers as `/var/lib/orthanc/db`. Alternatively, the original mock dataset is also available at <https://emckclac-my.sharepoint.com/:u:/g/personal/k2481169_kcl_ac_uk/ETiafC8VeqdIiQrChm208swBUABVQ_PYDomPRxLQcXvfkw?e=z1UaHp>.
