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

The local hub needs **no cloud account**. Sign-in is a local Keycloak container,
email is printed to the flip-api log, and model files, FL app bundles and
results live in a local S3-compatible object store (RustFS, data under
``./object-store/``).

AWS is still used for a few **artifact downloads by the two example Trusts**:

.. list-table::
   :header-rows: 1
   :widths: 35 35 30

   * - What
     - Why it needs AWS
     - Without access
   * - XNAT WAR and plugins
     - Fetched from ``FLIP_ARTIFACTS_BUCKET_NAME`` when the local plugin cache
       is missing or out of date
     - Trust XNAT cannot start; run the hub only (see below)
   * - OMOP core vocabulary
     - Licensed (SNOMED CT, LOINC, ...), so never published; fetched by
       ``make -C trust/omop-db load-omop-vocab`` from the AI Centre bucket
     - Download an equivalent export from `OHDSI Athena <https://athena.ohdsi.org/>`_
       under your own licence (``trust/omop-db/README.md``)

The mock patient data (OMOP rows and DICOM studies) does **not** need AWS: it
is downloaded anonymously from the public Hugging Face dataset
``aicentreflip/trust-data``.

If you do not have AWS access to the FLIP development account, you can still
run the hub on its own — ``make central-hub`` (Keycloak, database, object store,
API) or ``make up-no-trust`` (adds the FL servers). Neither touches AWS.

*************
Prerequisites
*************

- A Linux host. A CUDA GPU is only needed for GPU training; the stack itself
  runs on CPU.
- Docker Engine with the Compose plugin and Swarm mode (XNAT runs as a Swarm
  stack), plus the NVIDIA Container Toolkit on GPU hosts.
- GNU Make, ``jq``, ``curl``, and `uv <https://docs.astral.sh/uv/>`_ >= 0.10.0.
- A GitHub account with read access to the FLIP packages on GitHub Container
  Registry (``ghcr.io/londonaicentre``). Without it, use ``make up BUILD=true``
  to build every image locally.
- The `AWS CLI v2 <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html>`_
  and an AWS IAM Identity Center (SSO) user in the FLIP development account —
  only for the example Trusts, see above. Ask a FLIP maintainer for access.
- Free host ports for the hub (UI, ``8080`` API, ``5432`` database, ``8180``
  Keycloak, ``9000``/``9001`` object store) and for the two Trusts (their ports
  are listed in ``trust/.env.GSTT.development.example`` and
  ``trust/.env.KCH.development.example``).

The full tool list is in ``CONTRIBUTING.md`` ("Prerequisites").

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
     - A free port, e.g. ``44350``. The default ``443`` does **not** work:
       the dev UI serves plain HTTP.
   * - ``POSTGRES_PASSWORD``
     - Any password for the local hub database.
   * - ``ADMIN_USER_PASSWORD``
     - The password you will sign in with (it is set on every built-in dev
       user in Keycloak).
   * - ``AES_KEY_BASE64``
     - A new 32-byte key:
       ``python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"``
   * - ``FLIP_ARTIFACTS_BUCKET_NAME``
     - The XNAT artifacts bucket in the development account (ask a maintainer).
   * - ``AICENTRE_BUCKET_NAME``
     - Leave the placeholder. Only staging/production kit uploads use it; the
       local kits are provisioned in-tree (Step 5).
   * - ``NLB_SUBDOMAIN``
     - Leave the placeholder. A local stack never uses it, but the variable
       must exist.
   * - ``DEMO_*_PASSWORD``
     - Only for ``make demo-video``; any value.

Leave these alone: ``INTERNAL_SERVICE_KEY`` and ``INTERNAL_SERVICE_KEY_HASH``
(``make up`` generates them), the Cognito and SES settings (staging and
production only) and the object-store keys (defaults are in the Makefile).

.. warning::

   Put comments on their own line. Make keeps the spaces before an inline
   ``#`` as part of the value, so ``AWS_REGION=eu-west-2   # London`` breaks
   the stack.

You do **not** need to create the Trust files by hand. ``make up`` copies
``trust/.env.GSTT.development.example`` and ``trust/.env.KCH.development.example``
to ``trust/.env.<CODE>.development`` on the first run and fills in each
Trust's credentials when it registers it with the hub.

*************************************
Step 3 — Set up AWS SSO (Trusts only)
*************************************

Skip this step if you only run the hub.

1. Configure an SSO profile named ``dev`` for the FLIP development account:

   .. code-block:: bash

      aws configure sso

   Use the SSO start URL and region a maintainer gives you, pick the
   development account and your role, and name the profile ``dev``. The result
   in ``~/.aws/config`` looks like:

   .. code-block:: ini

      [profile dev]
      sso_session = FLIP
      sso_account_id = <dev-account-id>
      sso_role_name = <your-role>
      region = eu-west-2
      output = json

2. Sign in (repeat when the session expires, usually after a working day):

   .. code-block:: bash

      aws sso login --profile dev

3. Point the FLIP Makefiles at the profile — either uncomment
   ``AWS_PROFILE=dev`` in ``.env.development`` or ``export AWS_PROFILE=dev``
   in your shell — then check it:

   .. code-block:: bash

      make check-aws-access

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

*******************************************
Step 5 — Prepare Docker and the FL networks
*******************************************

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
and the object-store buckets, starts the hub, registers GSTT and KCH with it,
writes their kit files and starts both Trust stacks with their XNATs. Each
Trust is then seeded with the mock data pinned in ``trust/.data_version``.

The first run takes a while: images are pulled and about 2 GB of DICOM per
Trust is loaded into Orthanc. Later runs skip anything already seeded.

To start only part of the stack:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Command
     - Starts
   * - ``make central-hub``
     - Keycloak, database, object store and flip-api (no AWS)
   * - ``make up-no-trust``
     - The hub plus the FL API and FL servers (no AWS)
   * - ``make up-trusts``
     - The Trusts only, against an already-running hub

*********************************
Step 7 — Load the OMOP vocabulary
*********************************

The published OMOP data is vocabulary-free (the vocabulary is licensed). Load
it once per Trust, then restart the data-access APIs so they drop cached
results:

.. code-block:: bash

   make -C trust/omop-db load-omop-vocab                    # GSTT (port 5434)
   make -C trust/omop-db load-omop-vocab OMOP_DB_PORT=5436  # KCH

   docker restart trust1-data-access-api-1 trust2-data-access-api-1

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
- Object store console: ``http://localhost:9001``

Sign in as ``aicentreflip@gmail.com`` with your ``ADMIN_USER_PASSWORD``. Other
built-in users are listed in ``flip-api/src/flip_api/utils/constants.py``. MFA
is off in development.

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
     - Restart the hub API
   * - ``make up BUILD=true``
     - Rebuild images after a dependency or Dockerfile change. Ordinary
       source edits are bind-mounted and reload live.
   * - ``make debug SERVICE=flip-api``
     - Attach a debugger on port 5678 (see ``DEBUG.md``)
   * - ``make up FL_BACKEND=flower``
     - Use Flower instead of NVFLARE; provision it first with
       ``make -C fl-services/flower provision NET_NUMBER=1`` (and ``2``)
   * - ``make clean-object-store``
     - Empty the local object store

***************
Troubleshooting
***************

``make up`` stops at the FL kit check
   Run ``make -C fl-services/nvflare provision-2-nets`` (Step 5).

Image pulls fail with ``denied`` or ``unauthorized``
   Log in to GHCR (Step 4), or use ``make up BUILD=true``.

XNAT fails with ``FLIP_ARTIFACTS_BUCKET_NAME ... placeholder`` or an AWS error
   Set the bucket in ``.env.development``, run ``aws sso login --profile dev``
   and check ``make check-aws-access`` (Step 3).

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

- ``CONTRIBUTING.md`` — environment details, tests and the contribution flow
- ``trust/README.md`` — the Trust stack, kit files and data seeding
- ``deploy/README.md`` — the Compose files and running a second stack
- :doc:`../working-with-flip-apps` — build your own FL application
