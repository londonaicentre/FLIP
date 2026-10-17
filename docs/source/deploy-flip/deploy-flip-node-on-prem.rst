.. _deploy-flip-node-on-prem:

###############################
Deploy a FLIP node on-prem
###############################

An on-prem FLIP node runs the trust-side stack (trust-api, imaging-api,
data-access-api, FL client, optional XNAT/Orthanc) on an Ubuntu host owned by
the Trust. The node polls the Central Hub for tasks over HTTPS — all
communication with the hub is outbound and no inbound ports are opened to the internet.
Retrieval from the trust's own PACS is the exception — see
:doc:`../components/component-pacs`. This is the deployment
model used when the Trust has direct, governed access to its own OMOP database
and PACS. For deployment inside a TRE see :doc:`deploy-flip-node-in-tre`; for
the Central Hub side see :doc:`deploy-central-hub`.

.. contents:: On this page
   :local:
   :depth: 2

************
Architecture
************

.. code-block:: text

            Internet
                │
                ▼
        ┌──────────────────┐
        │   AWS Central     │
        │   Hub             │
        └─▲──────────────▲─┘
          │              │
   polls  │              │  polls
  (HTTPS) │              │ (HTTPS)
          │              │
   ┌──────┴───┐    ┌─────┴───────┐
   │ Trust A   │    │ Trust B      │
   │ (AWS EC2) │    │ (on-prem)    │
   └──────────┘    └─────────────┘

Each on-prem Trust host runs the same Docker Compose stack used on cloud
trusts. Container ports are not published on the host (the local-trust compose
file communicates over the internal Docker network only), so the stack can
coexist with the dev compose stack on the same machine without port conflicts.

+-----------------+----------------+--------------------------------------------+
| Service         | Container port | Protocol                                   |
+=================+================+============================================+
| trust-api       | 8000           | HTTP (polls hub outbound)                  |
+-----------------+----------------+--------------------------------------------+
| imaging-api     | 8000           | HTTP (internal)                            |
+-----------------+----------------+--------------------------------------------+
| data-access-api | 8000           | HTTP (internal)                            |
+-----------------+----------------+--------------------------------------------+
| fl-client       | —              | TCP (outbound to FL server via NLB)        |
+-----------------+----------------+--------------------------------------------+

*************
Prerequisites
*************

**Operator workstation** (the machine you run commands from — typically your
laptop):

- Python 3.12 or 3.13 and `UV <https://docs.astral.sh/uv/guides/install-python/>`_.
- Ansible (installed automatically by ``uv sync`` inside ``deploy/providers/AWS/``).
- Terraform outputs available — you must have already run ``make init`` and
  ``make apply`` in ``deploy/providers/AWS/`` (the Central Hub deployment).
- SSH access to the trust host, if provisioning remotely.

**Trust host** — an Ubuntu 22.04+ machine (physical or VM) with:

- A user account with ``sudo`` privileges (default: ``ubuntu``).
- SSH access from the operator workstation (if remote), or local access.
- Internet connectivity (to pull Docker images and packages).
- A writable directory for the FLIP application (default ``/opt/flip``).
- For GPU training or evaluation: an NVIDIA GPU on NVIDIA driver **580 or newer**, with the
  `NVIDIA Container Toolkit <https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html>`_.
  The FL images ship PyTorch built for CUDA 13, which bundles its own CUDA runtime, so no CUDA
  toolkit is needed on the host; check the "Driver Version" reported by ``nvidia-smi``. The
  playbook installs neither the driver nor the toolkit. A CPU-only host (kit
  ``NUM_AVAILABLE_GPUS=0``) needs neither.

**Trust data** — the two systems the FLIP node reads. Both must already exist
and already agree with each other before the node is deployed:

- An **OMOP database** (PostgreSQL) holding the trust's clinical data in the
  ``omop`` schema — OMOP CDM 5.4 plus the MI-CDM imaging tables
  (``image_occurrence``, ``image_feature``). FLIP reads it through a read-only
  role and writes nothing back.
- A **PACS** that answers a study-level query by accession number. The
  checklist to agree with your PACS manager is in
  :doc:`../components/component-pacs`.
- **The accession number is the link between the two.** For every imaging row
  in ``image_occurrence``, the ``accession_id`` column must hold the accession
  number the PACS answers to for that study — the DICOM Accession Number
  ``(0008,0050)``. This column is FLIP's own addition to MI-CDM, so a standard
  OMOP ETL neither creates nor fills it: your data team adds it and populates
  it as part of the OMOP load (:ref:`omop-accession-id` has the one-line DDL
  and what to populate it from). A row may exist per study or per series — the
  same accession repeats on each series row and FLIP imports the study once —
  but the value must be exactly one study's accession number — never empty,
  and never containing ``*``, ``?`` or ``\``: to a PACS those are queries that
  match many studies, not one, so FLIP refuses to send them — such a row is
  dropped from the pull with a warning in the trust's imaging-api log, and the
  study it stood for is silently never pulled.

Confirm both with the data and PACS teams before deployment. Neither is checked
by the deployment itself; a gap surfaces only when the first project's imaging
pull runs.

**Central Hub deployed in AWS** — required so the trust can resolve the hub
URL and fetch the FL participant kit from S3. FL clients reach the FL server
through the networking account's edge (:doc:`deploy-central-hub-aws-lza`).
See :doc:`deploy-central-hub` for the shared prerequisites.

******************************************
Onboarding an on-prem trust (step by step)
******************************************

When the Central Hub is already deployed, an on-prem trust is onboarded
asynchronously: the FLIP admin (who holds prod AWS creds) scaffolds,
registers, and packages a kit; the operator (who does not) extracts it and
brings the stack up. There is no SSH path — each side runs its own step
locally. ``<CODE>`` is the trust's short code; the admin's kit file is
``trust/.env.<CODE>.prod``. On the trust host the kit is named by its FL kit slot
instead — ``<slot>``, the ``FL_KIT_SLOT`` step 2 writes (e.g. ``Trust_3``) — as
``trust/.env.<slot>.prod``.

**1. Scaffold the kit (FLIP admin).** On a workstation with prod AWS creds:

.. code-block:: shell

   make new-trust TRUST_CODE=<CODE> TRUST_NAME="<trust name>" HUB=prod

This writes ``trust/.env.<CODE>.prod`` from the base template
(``trust/.env.example``): a host-local profile, mock-stack default
credentials, and ``<run-make-register-trust>`` placeholders for the
hub-managed blocks.

**2. Register the kit on the prod hub (FLIP admin).**

.. code-block:: shell

   make -C deploy/providers/AWS register-trusts KIT=<CODE> HUB=prod

This registers the trust on the prod hub and fills the Kit credentials
(``TRUST_API_KEY``, ``TRUST_INTERNAL_SERVICE_KEY``, ``FL_KIT_SLOT``,
``FL_KIT_SLOT_NUMBER``, ``EXPECTED_TRUST_ID``) plus as much of the Hub-shared
block as the hub task's environment carries. The block is built from the
environment of the one-off ``flip-api`` Fargate task
(``flip-api/src/flip_api/scripts/register_trust.py``, ``HUB_SHARED_ENV_KEYS`` — a filtered
comprehension that silently skips absent keys), and that task definition
(``deploy/providers/AWS/locals.tf``) carries only ``TRUST_API_KEY_HEADER`` and
``FL_BACKEND`` of the eleven: ``AES_KEY_BASE64`` is read from Secrets Manager at
use time and never placed in the environment, and ``CENTRAL_HUB_API_URL``, the
image tags, the two kit dates, ``NLB_SUBDOMAIN``, ``FL_SERVER_PORT`` and
``FL_EDGE_PORT`` are not in the task definition at all. Those nine stay at their
placeholder until the next step. The hub stores only SHA-256 hashes of the
credentials and cannot re-emit them, so the credentials half is write-once —
re-register to rotate.

**2b. Fill the rest of the Hub-shared block (FLIP admin, required).** From the
repo root, with the admin's local ``.env.prod`` present:

.. code-block:: shell

   make sync-trust-kit KIT=<CODE> HUB=prod

This upserts the Hub-shared block from the local env file and preserves the
credentials written in step 2. Skip it and the operator receives a kit whose
``AES_KEY_BASE64`` and ``CENTRAL_HUB_API_URL`` are still placeholders — a trust
that cannot decrypt a task or reach the hub. The same behaviour is described
from the hub side in ``deploy/providers/AWS/README.md`` ("Registering trusts
against the ECS hub").

**3. Package the kit (FLIP admin).** Tarball the populated kit file + the
operator's slice of the FL participant kit:

.. code-block:: shell

   make -C deploy/providers/AWS package-onprem-trust-kit KIT=<slot> KIT_FILE=$PWD/trust/.env.<CODE>.prod HUB=prod

The packager is keyed on the slot, so it takes the kit by path and ships it as
``.env.<slot>.prod``. It reads ``FL_BACKEND`` / ``FL_KIT_SLOT`` out of the kit
(it does not edit the file), slices only this
operator's portion of the FL participant kit out of S3
(``net-1/services/<slot>/`` for nvflare; ``net-1/certificates/`` + one
``supernode_credentials_<N>`` for flower), and writes a tarball under
``deploy/providers/AWS/build/trust-kits/``. Send it to the operator over an
encrypted channel — it contains a plaintext API key, AES encryption key, and
an FL TLS private key.

**4. Extract, configure, and start (trust operator).** Extract the tarball,
copy the kit into the checkout — a checkout at the **release the hub runs**
(``git fetch --tags origin && git checkout v<X.Y.Z>``; the admin tells you which,
and the hub's ``/api/health`` reports it), never a branch tip, because the
compose files and Makefiles that run the images come from it — edit **only**
the Host-local profile, then bring the stack up:

.. code-block:: shell

   tar -xzf flip-trust-kit-<slot>-<date>.tar.gz
   cp .env.<slot>.prod trust/
   # Edit trust/.env.<slot>.prod (Host-local profile section only):
   #   - FL_KIT_DIR=<path to extracted fl-kit>
   #   - adjust ports / bind-mount dirs to match your host
   #   - rotate the Trust-local passwords
   sudo -E make onboard-onprem-trust KIT=<slot>   # readiness checklist (HUB=prod by default)
   sudo -E make up-onprem-trust KIT=<slot>         # comes up after all checks pass

Both run via ``sudo``: the provisioning playbook deliberately does not add the
login user to the ``docker`` group (membership is root-equivalent), and the
checklist's docker-swarm row needs the docker socket.

``make onboard-onprem-trust`` prints a ✅/❌ checklist (docker swarm state,
Hub-shared / Kit credentials populated, FL_KIT_DIR set and on disk, FL kit
contents present) — fix any ❌ rows before ``up-onprem-trust``,
which gates on the same checks. The checklist is read-only, so you can run it
at any point, including before the kit is fully staged.

The prod trust compose mounts the extracted ``net-1/`` hierarchy into the
fl-client container, so preserve it as extracted.

.. note::

   **Upgrading a trust deployed before the XNAT port split (FLIP#993) — every
   existing trust must act.** ``XNAT_PORT`` used to mean both the DICOM SCP
   receiver and the web UI. It now means the receiver only, and the web UI has
   its own ``XNAT_WEB_PORT``. Both are published on the host on **every**
   deployment, not only where a real PACS is connected, so the two must differ.
   A kit that sets only ``XNAT_PORT`` resolves them to the same number — the
   web port defaults to ``XNAT_PORT`` — so your next ``up-onprem-trust`` is
   **refused**, naming both values and the file to edit. That refusal is the
   upgrade path, not a fault: it asks you to allocate a second port rather than
   moving your web UI to a number nothing else expects. Add ``XNAT_WEB_PORT``
   to the Host-local profile before your next deployment; the shipped
   allocation is 8104 for DICOM and 8105 for the web UI.

**Optional — set your site privacy policy (NVFLARE backend).** As the data
holder you can enforce your own update-privacy filter on everything the
fl-client sends to the FL server, independently of the researcher's app
configuration: uncomment ``FL_SITE_PRIVACY_POLICY`` (and optionally its
parameters) in the Host-local profile of ``trust/.env.<slot>.prod``.
``make onboard-onprem-trust`` validates the values (an invalid combination
stops the fl-client at startup, by design), and after ``up-onprem-trust`` the
fl-client log shows a ``[site-privacy] site privacy policy ACTIVE: ...`` line.
To change or drop the policy later, edit the kit file and restart the
fl-clients (``make -C trust up-fl-clients-kit KIT=<slot> HUB=prod``). See
:doc:`../components/component-fl-nets` ("Site-enforced privacy policy") for
the parameter reference and enforcement semantics.

Then verify the trust is polling: ``sudo docker logs -f trust<N>-trust-api-1`` should
show successful task polls against the Central Hub. No trust service sets
``container_name``, so compose names the container from the trust's project —
``trust<N>``, where ``<N>`` is the FL kit slot number the hub assigned
(``FL_KIT_SLOT_NUMBER`` in the kit file).

**Rotation (later, no re-mint).** ``sync-trust-kit`` is the same command that
completed the kit in step 2b, so when the admin later rotates a Hub-shared value
(``AES_KEY_BASE64``, image tags, ``FL_BACKEND``), re-run it to refresh only the
Hub-shared block from the admin's local env file — credentials are preserved:

.. code-block:: shell

   make sync-trust-kit KIT=<CODE> HUB=prod

Re-transmit the refreshed kit to the operator over the same encrypted channel. The
operator replaces only the Hub-shared block and re-applies it with
``sudo -E make upgrade-onprem-trust KIT=<slot> YES=1`` — not ``up-onprem-trust``, which
is the first-install verb and resets XNAT.

**Upgrading to a release (later).** Sites move between releases with
``sudo -E make upgrade-onprem-trust KIT=<slot>`` — the operator's command, defaulting to
the release the hub runs. See :doc:`/sys-admin/admin-upgrading-sites` for the runbook,
including when the admin has to re-issue kits first.

.. _onprem-core-vocabulary:

*****************************
Load the OMOP core vocabulary
*****************************

The node's OMOP database starts without the OMOP core vocabulary — SNOMED CT, LOINC and the other
licensed vocabularies that cohort queries resolve concepts through. Each site downloads its own copy
under its own licences: the vocabularies belong to their owners, and handing the AI Centre's copy to
another organisation would be redistribution those licences do not clearly allow.

**What to download.** At least the vocabularies in the
`core vocabulary roster <https://github.com/londonaicentre/FLIP/blob/develop/trust/omop-db/README.md#the-core-vocabulary-bundle>`_.

**Where from.** `OHDSI Athena <https://athena.ohdsi.org/>`_, with your own account, accepting each
vocabulary's licence there. The UK editions (SNOMED CT UK, dm+d, Read v2) come from
`NHS TRUD <https://isd.hscic.gov.uk/trud/>`_ under the NHS national licences. Unzip the export into
one directory: the loader reads the nine Athena table files from its top level (``CONCEPT.csv``,
``CONCEPT_ANCESTOR.csv``, ``CONCEPT_CLASS.csv``, ``CONCEPT_RELATIONSHIP.csv``,
``CONCEPT_SYNONYM.csv``, ``DOMAIN.csv``, ``DRUG_STRENGTH.csv``, ``RELATIONSHIP.csv``,
``VOCABULARY.csv``).

**Load it** once, after the first ``up-onprem-trust``, from the checkout on the trust host. It needs
``psql`` (``sudo apt-get install postgresql-client``); take the four values from your kit file:

.. code-block:: shell

   cd trust/omop-db
   OMOP_DB_HOST=127.0.0.1 OMOP_DB_PORT=<OMOP_DB_PORT> OMOP_POSTGRES_DB=<OMOP_POSTGRES_DB> \
     OMOP_POSTGRES_USER=<OMOP_POSTGRES_USER> OMOP_POSTGRES_PASSWORD='<OMOP_POSTGRES_PASSWORD>' \
     ./files/load_core_vocab.sh <export dir> files/OMOPCDM_postgresql_5.4_constraints.sql

The full roster takes about 25 minutes. Re-running is safe: tables that already hold the vocabulary are
skipped. With the same variables, ``./files/load_core_vocab.sh --check`` loads nothing and exits 0 once
every table is loaded. If a cohort query ran before the load, restart data-access-api
(``sudo docker restart trust<N>-data-access-api-1``): it caches query results and would replay the
empty answer.

On a Helm-deployed node, leave ``omopDb.vocabLoad.s3Bucket`` empty and run the same loader over a
``kubectl port-forward`` to the ``omop-db`` service, as the chart README
(``trust/deploy/helm/README.md``, "Local clusters (kind)") shows — with your own export in place of
its ``make fetch-vocab-core`` step.

**Without it** the node starts and passes every health check, but a cohort query that resolves
core-vocabulary concepts returns nothing.

***********************
Trust authentication
***********************

The Central Hub identifies a trust by its API key, not by IP address or
hostname — any host with the correct credentials in its ``.env`` can act as
that trust. The trust's env must contain:

+----------------------------------+--------------------------------------------------------+
| Variable                         | Purpose                                                |
+==================================+========================================================+
| ``EXPECTED_TRUST_ID`` (optional) | Opt-in self-check. If set and the hub resolves this    |
|                                  | host's API key to a different trust *id*, trust-api    |
|                                  | exits instead of acting as the wrong trust.            |
+----------------------------------+--------------------------------------------------------+
| ``TRUST_API_KEY``                | Per-trust secret used on every outbound call to the    |
|                                  | hub.                                                   |
+----------------------------------+--------------------------------------------------------+
| ``CENTRAL_HUB_API_URL``          | Public hub URL the trust polls (e.g.                   |
|                                  | ``https://app.flip.aicentre.co.uk``).                  |
+----------------------------------+--------------------------------------------------------+
| ``AES_KEY_BASE64``               | Symmetric key shared with the hub for the AES-256-GCM  |
|                                  | payload envelope. Must match the hub's key byte for    |
|                                  | byte; a mismatch fails every task as ``failed          |
|                                  | authentication``. Upgrade in step with the hub across  |
|                                  | FLIP#1179 (no CBC compatibility).                      |
+----------------------------------+--------------------------------------------------------+
| ``TRUST_INTERNAL_SERVICE_KEY``   | Per-trust shared secret used inside the trust for      |
|                                  | calls between trust-api / imaging-api / fl-client and  |
|                                  | imaging-api / data-access-api. Never leaves the trust. |
+----------------------------------+--------------------------------------------------------+

**Hub-side prerequisites** (must already be in place before the trust can
connect):

1. The trust must be registered on the hub — a row in the ``trust`` table with
   its ``api_key_hash`` — via ``make -C deploy/providers/AWS register-trusts
   KIT=<CODE> HUB=<hub>`` or the Add-Trust admin flow (``POST /admin/trusts``).
2. That same target mints the trust's API key and internal service key into the
   kit file (``trust/.env.<CODE>.prod`` for the on-prem trust) as part of
   registration.
3. No hub redeploy is needed — the trust registry is the live database, read
   on every request.

In the step-by-step flow above, the trust must be registered (step 2) before
the operator brings the stack up.

***********************
Network requirements
***********************

**No inbound port forwarding from the internet is needed.** (Retrieval from a trust PACS needs
one rule inside the trust's own network — see :doc:`../components/component-pacs`.) Trusts poll the hub outbound for
tasks, and FL clients connect outbound to the FL server through the networking
account's edge NLB. All communication is trust-initiated.

The trust host must be able to make outbound connections to two **separate** Central Hub
endpoints — different hostnames behind different load balancers, so both must be allowlisted:

- The Central Hub FLIP API over HTTPS (port 443) — the edge CloudFront (e.g. ``app.flip.aicentre.co.uk``).
- The FL Server endpoint over gRPC or HTTP (the kit's ``FL_EDGE_PORT``, else
  ``FL_SERVER_PORT``; 8002 on production, 8003 on staging) — the edge NLB
  (e.g. ``fl.app.flip.aicentre.co.uk``).

The hub keeps no per-trust allowlist, so a changed public IP needs no hub-side change.

***************
Troubleshooting
***************

+----------------------------------+------------------------------------------------------------+
| Symptom                          | Check                                                      |
+==================================+============================================================+
| Trust not polling hub            | Trust stack running? ``sudo docker ps`` on the trust host. |
|                                  | Check ``trust-api`` logs for polling errors.               |
+----------------------------------+------------------------------------------------------------+
| ``Connection timed out`` (FL)    | Host/router firewall blocking outbound on the FL port      |
|                                  | (8002 for production, 8003 for staging)?                   |
+----------------------------------+------------------------------------------------------------+
| Firewall blocking outbound       | Confirm the host/router firewall allows outbound HTTPS     |
|                                  | (443) and the FL gRPC port (``FL_EDGE_PORT``, def. 8002).  |
+----------------------------------+------------------------------------------------------------+
| Ansible ``Permission denied``    | SSH key correct? User has ``sudo``? ``ANSIBLE_BECOME_PASS``|
|                                  | set if running in local-host mode?                         |
+----------------------------------+------------------------------------------------------------+
