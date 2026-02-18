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

# Orthanc

Orthanc username and password are set by `ORTHANC_USERNAME` and `ORTHANC_PASSWORD` environment variables in [../../.env.development](../../.env.development).

Upload DICOM files to Orthanc, you can use the mock data in <https://emckclac-my.sharepoint.com/:u:/g/personal/k2481169_kcl_ac_uk/ETiafC8VeqdIiQrChm208swBUABVQ_PYDomPRxLQcXvfkw?e=z1UaHp>.

The Makefile has targets to download and extract the Orthanc data volumes from S3 (needs AWS credentials configured):

```sh
make sync-data-from-s3
make extract-data
```
