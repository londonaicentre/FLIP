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

.. _ts-fl-training-and-projects:

###############################
FL training and projects
###############################

Problems you see in the FLIP UI while a project is approved, its images are imported, or a model trains. Most of
them are fixed at a Trust, so each entry says who acts and links to the trust-side fix.

.. contents:: On this page
   :local:
   :depth: 2

******************************
Projects and the image import
******************************

Each Trust's import progress shows on the project page (:ref:`imaging-status`); start there to see which Trust
is stuck and in which state.

.. _ts-awaiting-creation:

A Trust stays on "Awaiting creation…"
=====================================

*Who: FLIP admin, with the trust operator*

**Cause.** The Trust has not yet created the project's imaging project in its XNAT. The Trust picks up the
request the next time it polls the hub; if that fails, the hub retries on a schedule. By default it looks for
projects with unimported studies every 30 minutes and re-imports any one project at most once an hour, so a retry
can take up to an hour and a half to appear.

**Fix.**

#. Open :ref:`connection-status`. An **Offline** Trust is not polling: its operator starts with the trust-api
   logs. A **Degraded** Trust names the failing service in its detail drawer.
#. If the Trust is online, its operator checks the XNAT entries for their deployment shape:

   - Compose: :ref:`ts-compose-xnat-setup-page`, :ref:`ts-compose-imaging-api-hung` and
     :ref:`ts-compose-xnat-401`.
   - Kubernetes: :ref:`ts-k8s-xnat-401`.

.. seealso::

   For a FLIP admin who needs to force the retry from the hub database, the SQL is in
   :gh:`deploy/providers/AWS/TROUBLESHOOTING.md` (section 3.3).

.. _ts-queuefailed:

Every study shows as QueueFailed right after approval
=====================================================

*Who: trust operator*

**Cause.** The Trust's PACS answered none of the cohort's accession numbers, so nothing was queued. Either the PACS
holds no studies (a mock Orthanc that was never seeded) or the ``accession_id`` values in the Trust's OMOP database
are not the accession numbers the PACS knows.

**Fix.**

- On the AWS-hosted trust, see :ref:`ts-compose-orthanc-empty`.
- On a real PACS, check the accession numbers with the data and PACS teams: :ref:`omop-accession-id` says what the
  column must hold.
- On Kubernetes, :ref:`ts-k8s-pacs-not-storable` also stops every request from being stored.

.. _ts-stuck-processing:

A Trust stays on "Processing" and re-import does nothing
========================================================

*Who: trust operator*

The imaging-api log shows ``No studies to retry import for project ...`` each time the re-import runs.

**Cause.** An earlier pull left request records in XNAT's database. imaging-api counts every study with such a
record as *Processing*, and re-imports only studies counted as *Failed* or *QueueFailed*, so the stale records block
the retry.

**Fix.** Delete the stale records, then trigger the re-import: :ref:`ts-k8s-force-repull`. On Compose, run the same
two ``DELETE`` statements with ``psql`` in the trust's ``xnat-db`` container.

******************
Training
******************

For a run that stalls, check the **Connection Status** page first (:ref:`connection-status`): it shows whether a
Trust's FL client and services are reporting.

.. _ts-num-samples-zero:

Training fails with ``num_samples=0``
=====================================

The model's log shows:

.. code-block:: text

   ValueError: num_samples should be a positive integer value, but got num_samples=0

The cohort itself is not empty, so this looks like a bug in the training code. It almost never is: the Trust's FL
client found no images to train on. Find which of these applies before changing anything.

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - What the Trust's logs show
     - Cause and fix
   * - imaging-api returns 500 with ``Permission denied`` on ``/app/data/images/net-<N>``
     - imaging-api cannot write the images directory: :ref:`ts-compose-images-permission`.
   * - Images imported, but scans have no NIfTI
     - DICOM-to-NIfTI conversion did not run. On Kubernetes see :ref:`ts-k8s-dcm2niix-not-registered`,
       :ref:`ts-k8s-dcm2niix-no-nifti` and :ref:`ts-k8s-dcm2niix-stale-image`; on Compose
       :ref:`ts-compose-dcm2niix`. Scans already archived without NIfTI need :ref:`ts-k8s-nifti-backlog`.
   * - Images and NIfTI present; the app reports ``No image/label pairs found``
     - Labels were never uploaded: see :ref:`enrichment-troubleshooting`.

.. _ts-fl-client-401:

The FL client gets ``401 Client Error: Unauthorized`` from data-access-api or imaging-api
=========================================================================================

*Who: trust operator*

.. code-block:: text

   requests.exceptions.HTTPError: 401 Client Error: Unauthorized for url: http://data-access-api:8000//cohort/dataframe

Calls from trust-api work; only the FL client's calls fail.

**Cause.** Every call inside a Trust carries the Trust's internal service key, and data-access-api and imaging-api
refuse a call without a matching one. An FL client image older than the trust APIs does not send the key at all.

**Fix.** Run the same release on every service of the Trust. Move the whole site to the release its hub runs with
the upgrade command for its deployment shape (:ref:`admin-upgrading-sites`).

.. seealso::

   :ref:`trust-internal-service-authentication` for how the key is used.

.. _ts-model-queued:

A model stays on ``Model Queued``
=================================

**Cause.** Each net trains one job at a time, first come, first served. The number in brackets is the model's
place in the queue; position 1 starts next.

**Fix.** Wait, or ask your FLIP administrator which job holds the net. You can withdraw your own queued model with
**Abort job** (:ref:`user-training`).

.. _ts-waiting-for-resources:

The activity feed shows "Waiting for resources · this trust cannot provide 1 GPU"
=================================================================================

**Cause.** On NVIDIA FLARE, the job asked each Trust for more GPUs than that Trust's FL client declares. FLIP never
lowers the request to fit. NVIDIA FLARE retries for about 40 minutes, then marks the model ``ERROR`` and frees the
net.

**Fix.** Lower the job's GPU request, or ask the Trust whether it has the GPUs. See
"GPU requirements and FLARE's scheduler" on :ref:`flip-fl-nets`.

.. _ts-run-never-starts:

The run never starts a round, or the Trust reports ``cannot sync with server Runner``
=====================================================================================

**Cause.** The app downloads something while it runs, typically pretrained weights (``pretrained=True``,
``torch.hub.load(...)``, ``from_pretrained(...)``). The FL server and Trust hosts have no internet route, so the
download hangs. On NVIDIA FLARE the Trust reports ``cannot sync with server Runner``; on Flower the run never starts
a round.

**Fix.** Upload the weights as one of the model's files and load them from beside your code. If the server-side
process dies, the hub's job watchdog sets the model to ``ERROR`` and frees the net.

.. seealso::

   :ref:`model-files`, and the job watchdog on :ref:`flip-fl-nets`.

.. _ts-fl-target-unreachable:

The Trust's FL client logs ``cannot send to 'server': target_unreachable``
==========================================================================

*Who: trust operator*

**Cause.** Usually the FL server restarted, for example during a hub deployment. The NVIDIA FLARE client
reconnects on its own, retrying every 10 seconds.

**Fix.** Wait a minute. If the client never reconnects:

- On an on-prem host, check that the host and network firewalls still allow outbound traffic on the FL port (see
  :ref:`deploy-trust-node-network`).
- On Kubernetes, check the egress policy: :ref:`ts-k8s-fl-port-blocked`.
- A FLIP admin can confirm the server sees the client:

  .. code-block:: shell

     aws logs tail /ecs/fl-server-net-1 --since 10m --profile <aws-profile> | grep -E 'Client|Re-activate'
