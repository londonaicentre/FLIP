..
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

.. _deploy-local-dev:

################################
Run FLIP locally for development
################################

This page walks you from a fresh clone to a working FLIP on one Linux host: a
**Central Hub** plus the two shipped example Trusts, **GSTT** and **KCH**, each
with its own trust APIs, OMOP database, mocked PACS (Orthanc), XNAT and FL
client. Everything runs in Docker and is started with one command,
``make up``.

It is a development and debugging setup, not a production deployment. For the
hub on AWS see :doc:`deploy-central-hub`; for a real Trust see
:doc:`deploy-flip-node-on-prem` or :doc:`deploy-flip-node-in-tre`.

.. contents:: On this page
   :local:
   :depth: 1

***********************************
What still needs AWS (and what not)
***********************************

Neither the hub nor the two example Trusts need a **cloud account**. Sign-in is
a local Keycloak container, email is printed to the flip-api log, and model
files, FL app bundles and results live in a local S3-compatible object store
(RustFS, data under ``./object-store/``). The Trusts' XNAT WAR and plugins are
public upstream downloads, and the mock patient data (OMOP rows and DICOM
studies) comes anonymously from the public Hugging Face dataset
``aicentreflip/trust-data``.

AWS is used for one optional step: the **OMOP core vocabulary** (Step 7). It is
licensed (SNOMED CT, LOINC, ...), so it is never published, and
``make -C trust/omop-db load-omop-vocab`` fetches the AI Centre's copy from the
``vocab/`` prefix of a FLIP deployment's S3 bucket.

.. note::

   **External contributors can skip Step 3 entirely.** The SSO profile and the
   maintainer access it mentions only apply to the AI Centre team. Download an
   equivalent vocabulary export from `OHDSI Athena <https://athena.ohdsi.org/>`_
   under your own licence instead — see
   `the core vocabulary bundle <https://github.com/londonaicentre/FLIP/blob/develop/trust/omop-db/README.md#the-core-vocabulary-bundle>`__.

*************
Prerequisites
*************

- A Linux host. A CUDA GPU is only needed for GPU training; the stack itself
  runs on CPU.
- Docker Engine with the Compose plugin and Swarm mode (XNAT runs as a Swarm
  stack), plus the NVIDIA Container Toolkit on GPU hosts.
- GNU Make, ``jq``, ``curl``, and `uv <https://docs.astral.sh/uv/>`_ (minimum
  version in ``CONTRIBUTING.md``).
- A GitHub account with read access to the FLIP packages on GitHub Container
  Registry (``ghcr.io/londonaicentre``). Without it, use ``make up BUILD=true``
  to build every image locally.
- Optional, AI Centre team only: the `AWS CLI v2 <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html>`_
  and an AWS IAM Identity Center (SSO) user with read access to the bucket
  holding the OMOP vocabulary (see above).
- Free host ports for the hub (UI, ``8080`` API, ``5432`` database, ``8180``
  Keycloak, ``9000``/``9001`` object store) and for the two Trusts (their ports
  are listed in ``trust/.env.GSTT.development.example`` and
  ``trust/.env.KCH.development.example``).

The full tool list is in `CONTRIBUTING.md <https://github.com/londonaicentre/FLIP/blob/develop/CONTRIBUTING.md#prerequisites>`__.

**************************
Step 1 — Clone the project
**************************

.. code-block:: bash

   git clone https://github.com/londonaicentre/FLIP.git
   cd FLIP
   git checkout develop

Run every command below from the repository root, as your own user (never
with ``sudo`` — the containers run with your UID).

************************************
Step 2 — Create the environment file
************************************

All hub settings live in one file, ``.env.development``. Start from the example:

.. code-block:: bash

   cp .env.development.example .env.development

The example has safe defaults for almost everything. Replace these
placeholders:

.. list-table::
   :header-rows: 1
   :widths: 32 68

   * - Variable
     - What to set
   * - ``UI_PORT``
     - The host port for the UI. The default ``443`` works; set another free
       port (e.g. ``44350``) if 443 is taken or you run a second stack.
   * - ``POSTGRES_PASSWORD``
     - Any password for the local hub database.
   * - ``ADMIN_USER_PASSWORD``
     - The password you will sign in with (it is set on every built-in dev
       user in Keycloak).
   * - ``AES_KEY_BASE64``
     - A new 32-byte key:
       ``python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"``

Leave these alone: ``INTERNAL_SERVICE_KEY`` and ``INTERNAL_SERVICE_KEY_HASH``
(``make up`` generates them), the Cognito and SES settings (staging and
production only) and the object-store keys (defaults are in
``deploy/compose.development.yml``).

.. warning::

   Put comments on their own line: an inline ``#`` comment becomes part of
   the value (the header of ``.env.development.example`` explains why).

You do **not** need to create the Trust files by hand. ``make up`` copies
``trust/.env.GSTT.development.example`` and ``trust/.env.KCH.development.example``
to ``trust/.env.<CODE>.development`` on the first run and fills in each
Trust's credentials when it registers it with the hub.

*****************************************
Step 3 — Set up AWS SSO (vocabulary only)
*****************************************

Skip this step unless you are on the AI Centre team and want the vocabulary
from its bucket (Step 7).

1. Configure an SSO profile for the AWS account whose bucket holds the
   vocabulary. A maintainer tells you the SSO start URL, the account, the role
   and the bucket name:

   .. code-block:: bash

      aws configure sso

   Name the profile whatever you like; ``aws configure sso`` writes both the
   ``[profile …]`` block and the ``[sso-session …]`` block it points at, so
   don't hand-write them.

2. Sign in (repeat when the session expires, usually after a working day):

   .. code-block:: bash

      aws sso login --profile <your-profile>

3. Export the profile and the bucket in your shell, then check access:

   .. code-block:: bash

      export AWS_PROFILE=<your-profile>
      export VOCAB_S3_BUCKET=<bucket-from-your-maintainer>
      make check-aws-access

   Export them rather than setting them in ``.env.development``: the root
   Makefile reads that file, so ``check-aws-access`` would pass, but
   ``make -C trust/omop-db load-omop-vocab`` never reads it and the vocabulary
   fetch would fail. There is no default bucket: the development AWS account
   that used to hold a copy has been retired (FLIP#1371).

The containers never see your AWS credentials; only the host-side download
steps use them.

*************************************
Step 4 — Log in to the image registry
*************************************

``make up`` pulls the published images from GHCR. Create a GitHub personal
access token with the ``read:packages`` scope and log in once:

.. code-block:: bash

   echo "$GHCR_PAT" | docker login ghcr.io -u <your-github-username> --password-stdin

Without GHCR access, add ``BUILD=true`` to the ``make up`` command in Step 6
to build the images from your checkout instead (slower).

*************************************************
Step 5 — Prepare Docker, XNAT and the FL networks
*************************************************

Download the Trusts' XNAT plugins from their public upstream URLs (checksums
pinned in ``trust/xnat/artifacts.manifest``). Do this once per checkout, and
again after a pull that changes that manifest — ``make up`` only checks the
cache and stops at the first Trust's XNAT if it is missing:

.. code-block:: bash

   make -C trust prepare-artifacts

FLIP does not redistribute the XNAT WAR or its plugins.
`trust/xnat/artifacts.manifest <https://github.com/londonaicentre/FLIP/blob/develop/trust/xnat/artifacts.manifest>`__
lists each file with its upstream URL and SHA-256. If this host cannot reach
those URLs (behind a hospital firewall, for example), download the ``plugin``
files listed there on another machine, keeping their upstream file names, put
them in one directory, and fill the cache from it. The same checksums are
verified:

.. code-block:: bash

   make -C trust prepare-artifacts ARTIFACTS_DIR=/path/to/dir

Once per Docker host, enable Swarm mode (if it is already active, Docker says
so and you can move on):

.. code-block:: bash

   docker swarm init

Then provision the two local federated-learning networks. ``make up`` does
**not** do this for you, and the FL containers cannot start without it:

.. code-block:: bash

   make -C fl-services/nvflare provision-2-nets

This writes the participant kits to the gitignored
``fl-services/nvflare/provision/workspace-dev/``.

***************************
Step 6 — Start the platform
***************************

.. code-block:: bash

   make up

In order, this generates the internal service key, creates the Docker networks
and the object-store buckets, starts the hub, registers GSTT and KCH with it
and writes their kit files. Then, one Trust at a time, it starts the Trust
stack, seeds it with the mock data pinned in ``trust/.data_version`` and starts
its XNAT.

The first run takes a while: images are pulled and each Trust's mock DICOM is
loaded into Orthanc. Later runs skip anything already seeded.

To start only part of the stack:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Command
     - Starts
   * - ``make central-hub``
     - Keycloak, database, object store and flip-api. Runs in the
       foreground and holds the terminal; skips the FL kit check.
   * - ``make up-no-trust``
     - The hub plus the FL API and FL servers. Still needs the FL networks
       provisioned (Step 5).
   * - ``make up-trusts``
     - The Trusts only, against an already-running hub

*********************************
Step 7 — Load the OMOP vocabulary
*********************************

The published OMOP data is vocabulary-free (the vocabulary is licensed). Load
it once per Trust, then restart the data-access APIs so they drop cached
results. The first load downloads the bundle from ``VOCAB_S3_BUCKET`` (Step 3)
into ``trust/omop-db/data/``; later loads reuse that copy:

.. code-block:: bash

   make -C trust/omop-db load-omop-vocab                    # GSTT (port 5434)
   make -C trust/omop-db load-omop-vocab OMOP_DB_PORT=5436  # KCH

   docker restart trust1-data-access-api-1 trust2-data-access-api-1

The container names assume the default stack; with ``FLIP_INSTANCE`` set they
carry its prefix (``<instance>-trust1-...``). ``docker ps`` lists them.

If you skip this, every cohort query returns zero rows, and later the project
cannot be staged with ``returned no cohort records (zero or
privacy-suppressed)`` — which looks like a privacy-threshold problem but is the
missing vocabulary.

**************************
Step 8 — Sign in and check
**************************

- UI: ``http://localhost:<UI_PORT>``
- API docs: ``http://localhost:8080/api/docs``
- Keycloak console: ``http://localhost:8180/admin`` (``admin`` / ``admin``)
- Object store console: ``http://localhost:9001`` (access key ``flip-dev``,
  secret key ``flip-dev-object-store`` unless you set
  ``OBJECT_STORE_ACCESS_KEY`` / ``OBJECT_STORE_SECRET_KEY``)

Sign in as ``aicentreflip@gmail.com`` with your ``ADMIN_USER_PASSWORD``. Other
built-in users are listed in ``flip-api/src/flip_api/utils/constants.py``. MFA
is off in development. The realm behind sign-in is described in
`deploy/keycloak/README.md <https://github.com/londonaicentre/FLIP/blob/develop/deploy/keycloak/README.md>`__.

To prove the whole chain works — create a project, run a cohort query, import
images, train, download results — run the end-to-end smoke test (several
minutes):

.. code-block:: bash

   make e2e_smoke

**************
Day-to-day use
**************

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Command
     - Does
   * - ``make down``
     - Stop everything (data is kept)
   * - ``make restart-no-trust``
     - Restart the hub API. Runs in the foreground, and removes the running
       FL API and FL server containers; ``make up-no-trust`` brings them back.
   * - ``make up BUILD=true``
     - Rebuild images after a dependency or Dockerfile change. Ordinary
       source edits are bind-mounted and reload live (see
       `Running the stack <https://github.com/londonaicentre/FLIP/blob/develop/CONTRIBUTING.md#running-the-stack-pull-vs-build>`__).
   * - ``make debug SERVICE=flip-api``
     - Attach a debugger on port 5678 (see `DEBUG.md <https://github.com/londonaicentre/FLIP/blob/develop/DEBUG.md>`__)
   * - ``make up FL_BACKEND=flower``
     - Use Flower instead of FLARE; provision it first with
       ``make -C fl-services/flower provision NET_NUMBER=1`` (and ``2``). See
       the `Flower service guide <https://github.com/londonaicentre/FLIP/blob/develop/fl-services/flower/README.md>`__.
   * - ``make clean-object-store``
     - Empty the local object store

***************
Troubleshooting
***************

``make up`` stops at the FL kit check
   Run ``make -C fl-services/nvflare provision-2-nets`` (Step 5).

Image pulls fail with ``denied`` or ``unauthorized``
   Log in to GHCR (Step 4), or use ``make up BUILD=true``.

``make up`` stops at the first Trust's XNAT (``xnat-plugins-check``)
   The XNAT plugin cache is missing or older than
   ``trust/xnat/artifacts.manifest``. Run ``make -C trust prepare-artifacts``
   (Step 5) and ``make up`` again.

The UI loads but sign-in fails
   Check ``UI_PORT`` is the port in your browser's address bar, and that
   nothing else is using port ``8180``.

Cohort queries return no records
   Load the OMOP vocabulary (Step 7).

``permission denied`` on ``object-store/`` or ``jobs/``
   A container created the directory as root, usually after a plain
   ``docker compose up``. Fix the owner with
   ``sudo chown -R "$(id -u):$(id -g)" object-store jobs`` and use ``make up``.

****************
Where to go next
****************

- `CONTRIBUTING.md <https://github.com/londonaicentre/FLIP/blob/develop/CONTRIBUTING.md>`__ — environment details, tests and the contribution flow
- `trust/README.md <https://github.com/londonaicentre/FLIP/blob/develop/trust/README.md>`__ — the Trust stack, kit files and data seeding
- `deploy/README.md <https://github.com/londonaicentre/FLIP/blob/develop/deploy/README.md>`__ — the Compose files and running a second stack
- `trust/deploy/ansible/README.md <https://github.com/londonaicentre/FLIP/blob/develop/trust/deploy/ansible/README.md>`__ — provisioning an on-prem Trust host
- :doc:`../working-with-flip-apps` — build your own FL application
