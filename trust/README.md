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

# Mock trust setup

Deploy components at the trust level:

* Orthanc ([orthanc](orthanc))
* Imaging API ([imaging-api](imaging-api))
* Data Access API ([data-access-api](data-access-api))
* Trust API ([trust-api](trust-api))
* nginx TLS termination ([nginx](nginx)) — terminates HTTPS on `TRUST_API_PORT` and proxies to `trust-api`
* OMOP Database ([omop-db](omop-db))
* XNAT ([xnat](xnat))

See also the dedicated README files under each folder.

## Setup

### TLS certificates

The Trust API is served over HTTPS via an nginx TLS termination proxy. Before starting the trust services
you must generate the TLS certificates for your machine's IP or hostname:

```sh
TRUST_HOST=<ip-or-hostname> make generate-trust-certs
```

This creates a local CA and a server certificate under `trust/certs/` (git-ignored). The CA certificate
(`trust-ca.crt`) must be distributed to the Central Hub so it can verify the Trust's self-signed cert —
see `deploy/providers/AWS/` for how this is done in production.

To verify the generated certificates:

```sh
make verify-trust-certs
```

To smoke-test the HTTPS endpoint after the services are running:

```sh
TRUST_HOST=<ip-or-hostname> make test-trust-https
```

### Start Orthanc and trust services

Orthanc, Imaging API, Data Access API and Trust API can be started using the Makefile provided at the repository level:

```sh
make up
```

DICOMs can be uploaded to Orthanc at <http://localhost:8042>.

The Trust API is accessible over HTTPS at `https://<TRUST_HOST>:<TRUST_API_PORT>`.

## OMOP Database

See dedicated README under [omop-db/README.md](omop-db/README.md) for instructions to populate the database.

## Start XNAT

See dedicated README under [xnat/README.md](xnat/README.md).
