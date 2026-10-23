..  Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

.. _onboarding-a-trust:

################
Onboard a trust
################

This is the runbook for adding a trust to a running FLIP network, on any platform. Steps 1 to 4 and 6 are the FLIP
admin's; steps 5, 7 and 8 are the trust operator's. Only step 5, the install, differs by platform, and it lives on
the platform's own page.

Before you start, check the requirements on :doc:`deploy-trust-node`.

.. contents:: On this page
   :local:
   :depth: 1

**************
How to read it
**************

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Placeholder
     - Meaning
   * - ``<CODE>``
     - The trust's short code, chosen in step 1.
   * - ``<slot>``
     - The FL kit slot the hub assigns in step 2 (``FL_KIT_SLOT`` in the kit file), for example ``Trust_2``.
   * - ``<N>``
     - The slot's number (``FL_KIT_SLOT_NUMBER`` in the kit file).

The commands target production (``PROD=true``, kit file ``trust/.env.<CODE>.production``). For staging use
``PROD=stag`` (``.stag``); on a Landing Zone Accelerator estate, ``PROD=lza`` (``.lza-prod``) or ``PROD=lza-stag``
(``.lza-stag``).

*******************************
Step 1 — Create the kit
*******************************

*Who: FLIP admin, from the repo root*

.. code-block:: shell

   make new-trust TRUST_CODE=<CODE> TRUST_NAME="<trust name>" PROD=true

This writes ``trust/.env.<CODE>.production`` with host defaults, placeholder local passwords and
``<run-make-register-trust>`` placeholders for the values the hub manages.

**************************************
Step 2 — Register the trust on the hub
**************************************

*Who: FLIP admin*

.. code-block:: shell

   make -C deploy/providers/AWS register-trusts KIT=<CODE> PROD=true

This registers the trust with the hub and writes the trust's **credentials** into its kit file (API key, internal
service key, FL slot and expected trust id). No hub redeploy is needed. Most of the **hub-shared settings**,
including the encryption key and the hub URL, are still placeholders afterwards; step 3 fills them.

.. important::

   The hub keeps only hashes of these credentials and cannot show them again; registering the same trust again
   returns no new credentials. Keep the kit file safe. If it is lost, the trust must be deleted on the hub
   (``make -C deploy/providers/AWS delete-trust NAME=<trust name>``, which also removes its history there) and
   registered afresh — see :doc:`/sys-admin/admin-backup-and-restore`.

************************************************************
Step 3 — Fill in the hub-shared settings and XNAT passwords
************************************************************

*Who: FLIP admin, from the repo root with your local* ``.env.production`` *present*

.. code-block:: shell

   make sync-trust-kit KIT=<CODE> PROD=true
   make generate-xnat-credentials KIT=<CODE> PROD=true

The first command copies the hub-shared settings from your local env file into the kit and leaves the credentials
from step 2 untouched. The second mints the XNAT database and message-broker passwords, which registration on the
hub does not write.

.. important::

   Do not skip this step. Without it the kit's encryption key, hub URL and XNAT passwords stay as placeholders: the
   trust can neither reach the hub nor decrypt a task, and the kit can be neither packaged nor started.

.. seealso::

   :ref:`deploy-central-hub-register-trusts` for the same flow from the hub side.

*************************
Step 4 — Deliver the kit
*************************

*Who: FLIP admin*

Send the operator the kit and tell them which release the hub runs (its ``/api/health`` reports it): the operator
checks out that release before installing.

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Platform
     - What to send
   * - Docker Compose host (also a TRE)
     - A tarball with the kit file and this trust's share of the FL participant kit:

       .. code-block:: shell

          cp trust/.env.<CODE>.production trust/.env.<slot>
          make -C deploy/providers/AWS package-onprem-trust-kit KIT=<slot> PROD=true

       The packaging script reads the kit from ``trust/.env.<slot>`` and refuses to run if the slot in the file does
       not match ``KIT``, or if any placeholder is still unfilled. It fetches the FL kit share from S3 and writes the
       tarball under ``deploy/providers/AWS/build/trust-kits/``. It does not modify the kit file.
   * - Kubernetes
     - The kit file ``trust/.env.<CODE>.production`` and this trust's share of the FL participant kit. The operator
       stages the FL kit onto the node with ``stage-kit``.

.. warning::

   Either way, the delivery holds a plaintext API key, the encryption key and an FL TLS private key. Send it over an
   encrypted channel only.

*************************************
Step 5 — Install on your platform
*************************************

*Who: trust operator*

Follow your platform's page, then come back here for step 6:

- **Docker Compose host** (on-prem or in a TRE): :doc:`deploy-flip-node-on-prem`
- **Azure VM**: :doc:`deploy-flip-node-azure`
- **Kubernetes**: :doc:`deploy-flip-node-kubernetes`

***********************************************
Step 6 — Open the FL load balancer to the trust
***********************************************

*Who: FLIP admin.* Needed for FL training only; polling works without it.

The operator sends you the trust's public (egress) IP. Add it to the list for the trust's platform in your hub env
file (``.env.production``), then apply:

.. list-table::
   :header-rows: 1
   :widths: 22 30 48

   * - Platform
     - List (an HCL list)
     - Apply
   * - Docker Compose host
     - ``LOCAL_TRUST_PUBLIC_IPS=["1.2.3.4"]``
     - ``make -C deploy/providers/AWS allow-local-trust-nlb LOCAL_TRUST_IP=<public-ip> PROD=true``
   * - Kubernetes
     - ``K8S_TRUST_PUBLIC_IPS=["1.2.3.4"]``
     - ``make -C deploy/providers/AWS add-k8s-trust K8S_TRUST_IP=<node-public-ip> PROD=true``

Each command checks the IP is in the list, then runs a normal Terraform plan and apply. The rule is ordinary config,
so later full applies show no drift, and re-running with an IP already listed changes nothing.

On a Landing Zone Accelerator estate these commands create no rule: FLIP runs no FL load balancer of its own there,
and the FL entry point is the networking account's edge (:doc:`deploy-central-hub-aws-lza`).

.. note::

   **If the trust's public IP changes** (common on residential broadband), FL connections time out. Add the new IP
   to the list and apply again.

**************************************
Step 7 — Check the trust is polling
**************************************

*Who: trust operator*

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Platform
     - Command
   * - Docker Compose host
     - ``sudo docker logs -f trust<N>-trust-api-1``
   * - Kubernetes
     - ``kubectl logs -n flip-trust -l app.kubernetes.io/component=trust-api``

You should see successful polls against the Central Hub: ``POST .../api/trust/heartbeat`` and
``GET .../api/tasks/pending`` answered with ``200 OK``.

If nothing polls, check that the node is running and that the host and network firewalls allow outbound HTTPS (443)
and the FL port (see :ref:`deploy-trust-node-network`). Platform-specific checks are on
:ref:`ts-trust-node-compose` and :ref:`ts-trust-node-kubernetes`.

.. _onprem-core-vocabulary:

******************************************
Step 8 — Load the OMOP core vocabulary
******************************************

*Who: trust operator, once, after the first start*

The node's OMOP database starts without the OMOP core vocabulary: SNOMED CT, LOINC and the other licensed
vocabularies that cohort queries resolve concepts through. Each site downloads its own copy under its own licences;
the AI Centre cannot hand on its copy, because the licences do not clearly allow that redistribution.

.. warning::

   Without the vocabulary the node starts and passes every health check, but any cohort query that resolves
   core-vocabulary concepts returns nothing.

#. **Download the vocabularies.** You need at least those in the
   :gh:`core vocabulary roster <trust/omop-db/README.md>` ("The core vocabulary bundle").

   - From `OHDSI Athena <https://athena.ohdsi.org/>`_, with your own account, accepting each vocabulary's licence.
   - The UK editions (SNOMED CT UK, dm+d, Read v2) from `NHS TRUD <https://isd.hscic.gov.uk/trud/>`_, under the NHS
     national licences.

#. **Unzip the export into one directory.** The loader reads these nine files from its top level: ``CONCEPT.csv``,
   ``CONCEPT_ANCESTOR.csv``, ``CONCEPT_CLASS.csv``, ``CONCEPT_RELATIONSHIP.csv``, ``CONCEPT_SYNONYM.csv``,
   ``DOMAIN.csv``, ``DRUG_STRENGTH.csv``, ``RELATIONSHIP.csv`` and ``VOCABULARY.csv``.

#. **Load it.** You need ``psql`` (``sudo apt-get install postgresql-client``). From the ``trust/omop-db`` directory
   of your checkout, with the four ``OMOP_*`` values from your kit file:

   .. code-block:: shell

      OMOP_DB_HOST=127.0.0.1 OMOP_DB_PORT=<OMOP_DB_PORT> OMOP_POSTGRES_DB=<OMOP_POSTGRES_DB> \
        OMOP_POSTGRES_USER=<OMOP_POSTGRES_USER> OMOP_POSTGRES_PASSWORD='<OMOP_POSTGRES_PASSWORD>' \
        ./files/load_core_vocab.sh <export dir> files/OMOPCDM_postgresql_5.4_constraints.sql

   The full roster takes about 25 minutes. Re-running is safe: tables that already hold the vocabulary are skipped.
   On Kubernetes, run it over a ``kubectl port-forward`` to the ``omop-db`` service, or let the chart load it from
   an S3 bucket instead; see "Load the OMOP core vocabulary" on :doc:`deploy-flip-node-kubernetes`.

#. **Check it.** With the same variables, ``./files/load_core_vocab.sh --check`` loads nothing and exits 0 once every
   table is loaded.

#. **If a cohort query ran before the load,** restart data-access-api so it drops its cached (empty) results:

   .. list-table::
      :header-rows: 1
      :widths: 22 78

      * - Platform
        - Command
      * - Docker Compose host
        - ``sudo docker restart trust<N>-data-access-api-1``
      * - Kubernetes
        - ``kubectl -n flip-trust rollout restart deployment/trust-release-flip-trust-data-access-api``

The trust is now onboarded. The sections below are for later.

.. _onprem-site-privacy-policy:

**********************************************
Optional — set a site privacy policy (FLARE)
**********************************************

*Who: trust operator*

As the data holder, you can apply your own privacy filter to everything the FL client sends to the FL server,
whatever the researcher's app configures.

#. In your kit file, uncomment ``FL_SITE_PRIVACY_POLICY`` and, optionally, its parameters. An invalid combination
   stops the FL client at startup, by design.
#. Apply it:

   - **Docker Compose host:** ``sudo -E make onboard-onprem-trust KIT=<slot>`` from the repo root validates the
     settings; then restart the FL clients with ``sudo -E make -C trust up-fl-clients-kit KIT=<slot> PROD=true``.
   - **Kubernetes:** re-run the deploy (Step 4 on :doc:`deploy-flip-node-kubernetes`), which carries the settings
     into the chart.

#. The FL client log then shows ``[site-privacy] site privacy policy ACTIVE: ...``.

To change or remove the policy later, edit the kit file and apply it the same way.

.. seealso::

   :doc:`../components/component-fl-privacy`, "Site-enforced privacy policy", for the parameters and how they are
   enforced. A trust's governance document can set the same filter instead; set it in one place only.

**********************************
Later — rotate hub-shared settings
**********************************

When the admin changes a hub-shared value (the encryption key, image tags or FL backend), the credentials do not need
to be re-issued.

#. *FLIP admin:* refresh the kit from the repo root, then send it to the operator over the same encrypted channel.

   .. code-block:: shell

      make sync-trust-kit KIT=<CODE> PROD=true

#. *Trust operator:* replace only the *Hub-shared* block in your kit file, then apply it:

   - **Docker Compose host:** ``sudo -E make upgrade-onprem-trust KIT=<slot> YES=1`` from the repo root.
   - **Kubernetes:** re-run the deploy (Step 4 on :doc:`deploy-flip-node-kubernetes`); it re-patches the keys into
     the Secret and restarts the API services.

.. warning::

   On a Docker Compose host, do not use ``up-onprem-trust`` on a running trust. It is the first-install command and
   resets XNAT.

********************************
Later — upgrade to a new release
********************************

*Who: trust operator*

Check out the new release tag, then:

- **Docker Compose host:** ``sudo -E make upgrade-onprem-trust KIT=<slot>`` from the repo root.
- **Kubernetes:** ``make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> PROD=true``; see "Upgrading" on
  :doc:`deploy-flip-node-kubernetes`.

By default both move the site to the release the hub runs.

.. seealso::

   :doc:`/sys-admin/admin-upgrading-sites` for the full runbook, including when the admin must re-issue kits first.
