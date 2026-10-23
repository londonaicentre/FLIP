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

.. _ts-trust-node-kubernetes:

#########################
Trust node on Kubernetes
#########################

Problems on a trust node deployed with the ``flip-trust`` Helm chart
(:doc:`../deploy-flip/deploy-flip-node-kubernetes`).

**Names used below.** The commands assume the chart defaults: namespace ``flip-trust`` and release
``trust-release``, so resources are named ``trust-release-flip-trust-<service>``. Substitute yours if you changed
them. ``make -C …`` commands run from the repo root on the deployment host; ``<admin-pass>`` is the XNAT admin
password from the chart's secrets.

.. contents:: On this page
   :local:
   :depth: 2

.. _ts-k8s-checklist:

***********************
Quick checks, in order
***********************

.. code-block:: shell

   # 1. Pods and the release
   kubectl get pods -n flip-trust
   helm list -n flip-trust

   # 2. The chart's own checks: release status, XNAT plugin roster, smoke tests
   make -C trust/deploy/helm status
   make -C trust/deploy/helm smoke-cstore     # a real C-STORE through the PACS into XNAT

   # 3. OMOP data is there
   kubectl exec -n flip-trust trust-release-flip-trust-omop-db-0 -- \
     psql -U omop -d omop -c "SELECT COUNT(*) FROM omop.image_occurrence;"

   # 4. XNAT's retrieval queue and log
   kubectl exec -n flip-trust trust-release-flip-trust-xnat-db-0 -- psql -U xnat -d xnat -c \
     "SELECT status, COUNT(*) FROM xhbm_queued_pacs_request GROUP BY status;"
   XNAT_POD=$(kubectl get pods -n flip-trust -l app.kubernetes.io/component=xnat-web \
     -o jsonpath='{.items[0].metadata.name}')
   kubectl exec -n flip-trust "$XNAT_POD" -- tail -30 /data/xnat/home/logs/dqr.log

   # 5. FL client (if training does not start)
   FL_POD=$(kubectl get pods -n flip-trust -l app.kubernetes.io/component=fl-client \
     -o jsonpath='{.items[0].metadata.name}')
   kubectl logs -n flip-trust "$FL_POD" --tail=50

The entries below reuse ``$XNAT_POD``.

**********************
Pods and deployments
**********************

.. _ts-k8s-pods-not-starting:

A pod does not start
====================

Start with the pod's events and logs:

.. code-block:: shell

   kubectl describe pod <pod> -n flip-trust
   kubectl logs <pod> -n flip-trust                    # add -c <init-container> for an init container

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Symptom
     - Likely cause
     - Check
   * - ``Init:0/1``, ``Init:1/2``
     - An init container is still running or failed.
     - The init container's logs (``-c <name>``).
   * - ``CrashLoopBackOff``
     - The container exits at start: missing secret, wrong setting or unreachable database.
     - The container's logs.
   * - ``Pending``, with a volume event
     - A volume is not bound: no default StorageClass, or none matching ``storageClassName``.
     - ``kubectl get pvc -n flip-trust``
   * - ``Pending``, with a scheduling event
     - Not enough CPU or memory on the nodes.
     - The pod's events.
   * - FL client ``Pending`` with ``hostPath type check failed``
     - The FL participant kit is not on the node.
     - Step 3 of :doc:`../deploy-flip/deploy-flip-node-kubernetes`, then deploy again.
   * - ``xnat-web`` ``Running`` but ``0/1`` ready for several minutes
     - Its readiness check fails: ``000`` means XNAT is still starting; ``401`` or ``403`` means this XNAT version
       closed the path the check uses.
     - The command below.

.. code-block:: shell

   kubectl exec -n flip-trust deploy/trust-release-flip-trust-xnat-web -c tomcat -- \
     curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/xapi/siteConfig/buildInfo

.. seealso::

   The "Troubleshooting" section of the :gh:`chart README <trust/deploy/helm/README.md>` for pods stuck in
   ``Pending`` or ``CrashLoopBackOff``, a slow XNAT start and the FL client checklist.

.. _ts-k8s-secret-conflict:

Deploy refused: ``conflict with "kubectl-patch"``
=================================================

.. code-block:: text

   Error: UPGRADE FAILED: conflict occurred while applying object
   flip-trust/trust-release-flip-trust-secrets /v1, Kind=Secret:
   Apply failed with 3 conflicts: conflict with "kubectl-patch" using v1:
     .data.aes-key-base64
     .data.trust-api-key
     .data.trust-internal-service-key

Nothing changed in the cluster: the release stays on its previous, working revision.

**Cause.** Helm 4 applies the release server-side. The trust's three keys were written by ``patch-kit-secrets``,
and the values the chart would render from ``values-secrets.yaml`` differ from them, so Kubernetes reports the
conflict instead of overwriting the live keys.

**Fix.** Re-run the kit patch once. It realigns ``values-secrets.yaml`` with what it patched, so the next deploy
leaves those keys alone:

.. code-block:: shell

   make -C trust/deploy/helm patch-kit-secrets KIT=<CODE> PROD=<env>
   make -C trust/deploy/helm deploy-trust-k8s KIT=<CODE> PROD=<env>

.. warning::

   Do not force it:

   - ``helm upgrade --force-conflicts`` or ``--take-ownership`` overwrites the live keys with whatever
     ``values-secrets.yaml`` holds. An empty slot is left out of the Secret, so the trust can lose its API key and
     poll the hub with a dead credential.
   - ``kubectl apply --server-side`` of just these keys removes every other key that manager owns, emptying the
     XNAT, OMOP and Orthanc entries in the same Secret.

Helm 3 never raises this conflict.

.. _ts-k8s-recreate-forbidden:

Upgrade refused: ``rollingUpdate: Forbidden`` on a ``Recreate`` Deployment
==========================================================================

.. code-block:: text

   Error: UPGRADE FAILED: server-side apply failed for object flip-trust/trust-release-flip-trust-data-access-api
   apps/v1, Kind=Deployment: Deployment.apps "trust-release-flip-trust-data-access-api" is invalid:
   spec.strategy.rollingUpdate: Forbidden: may not be specified when strategy `type` is 'Recreate'

The release stays on its previous revision.

**Cause.** The chart now renders as ``Recreate`` a Deployment the cluster created as ``RollingUpdate``. FLIP#857
did this to data-access-api when its snapshot store is ReadWriteOnce. The API server filled in ``rollingUpdate``
defaults when the Deployment was created, and no field manager owns them, so Helm 4's server-side apply keeps them
beside the new ``type: Recreate`` and the object is invalid.

**Fix.** ``make deploy`` (and so ``deploy-trust-k8s`` and ``upgrade-trust-k8s``) switches such Deployments before
the upgrade (FLIP#1524, ``scripts/migrate-strategy.sh``). A plain ``helm upgrade`` does not; patch the named
Deployment once, then upgrade:

.. code-block:: shell

   kubectl -n flip-trust patch deploy trust-release-flip-trust-data-access-api --type=json \
     -p '[{"op":"remove","path":"/spec/strategy/rollingUpdate"},{"op":"replace","path":"/spec/strategy/type","value":"Recreate"}]'

Setting ``rollingUpdate: null`` in the chart does not help. Helm 3 applies client-side and never raises this.

.. _ts-k8s-xnat-init-timeout:

Deploy fails with ``resource Job/flip-trust/trust-release-flip-trust-xnat-init not ready``
==========================================================================================

**Cause.** The ``xnat-init`` job, which configures XNAT after each install or upgrade, ran longer than the deploy's
wait budget. Helm has already applied the new spec by then, so the error names Helm, not the wait.

**Fix.** Time one run of the job, then deploy with a larger budget (the default is ``30m``):

.. code-block:: shell

   kubectl get job -n flip-trust -w
   make -C trust/deploy/helm deploy-trust-k8s KIT=<CODE> PROD=<env> HELM_TIMEOUT=45m

If the job is wedged rather than slow, set ``xnat.initJob.enabled: false`` in your values file (``VALUES_FILE``, see
"Key values" on :doc:`../deploy-flip/deploy-flip-node-kubernetes`), deploy, and run
``make -C trust/deploy/helm xnat-init`` on its own, where its logs are easy to read. "The wait budget" under Step 4
of :doc:`../deploy-flip/deploy-flip-node-kubernetes` explains the setting.

****************************
DICOM import into XNAT
****************************

.. _ts-k8s-cstore-aborts:

C-ECHO passes, C-STORE aborts (``AbstractMethodError`` in ``dicom.log``)
========================================================================

``echoscu`` reaches the XNAT receiver, the release is ``deployed`` and XNAT's web UI answers, but every real
transfer ends with *Peer aborted Association* and nothing reaches the prearchive. XNAT's log shows the importer
crashing:

.. code-block:: shell

   kubectl exec -n flip-trust "$XNAT_POD" -- \
     grep -E 'AbstractMethodError|unable to read DICOM object null' /data/xnat/home/logs/dicom.log

**Cause.** The running ``xnat-web`` pod has older plugin jars than the chart lists. A C-ECHO never reaches XNAT's
importer, so it cannot detect this. The pod downloads its plugins afresh every time it is created, so wrong jars
mean the pod itself is old: no deploy has replaced it since the plugin list changed. Three reasons are common:

#. **The deploy never carried the new list**: an older checkout, or an overrides file pinning old versions. Check
   with ``helm get values trust-release -n flip-trust --revision <N> --all | grep -A5 urls``.
#. **The rollout stalled.** Releases deployed before the chart switched ``xnat-web`` to the ``Recreate`` strategy
   start the new pod beside the old one; it never gets the volume and the old pod keeps serving. Two ReplicaSets
   with a stuck pod in ``kubectl -n flip-trust get rs,pods -l app.kubernetes.io/component=xnat-web`` show this.
#. **The deploy never finished**, because the ``xnat-init`` job outran the wait: :ref:`ts-k8s-xnat-init-timeout`.

**Check.**

.. code-block:: shell

   kubectl exec -n flip-trust "$XNAT_POD" -- ls -la /data/xnat/home/plugins   # the jars the pod carries
   make -C trust/deploy/helm status          # fails naming both versions when the roster drifted

**Fix.** Get one deploy to complete, from a current checkout, with a wait budget above the init job's real
duration:

.. code-block:: shell

   make -C trust/deploy/helm deploy-trust-k8s KIT=<CODE> PROD=<env> HELM_TIMEOUT=45m

If a stalled pod from reason 2 is still stuck, delete it (``kubectl -n flip-trust delete pod <pod>``) so the new one
can take the volume. Then confirm the pod was replaced and send a test study:

.. code-block:: shell

   kubectl exec -n flip-trust "$XNAT_POD" -- ls -la /data/xnat/home/plugins   # dated today
   make -C trust/deploy/helm smoke-cstore

If ``smoke-cstore`` finds no receipt line and no new prearchive object, the study went to something other than
this XNAT: check the receiver first (:ref:`ts-k8s-pacs-not-storable`).

.. seealso::

   How ``smoke-cstore`` judges a run, and its exit codes and settings, are in
   :gh:`trust/deploy/helm/TROUBLESHOOTING.md` (section 2.7).

.. _ts-k8s-pacs-not-storable:

``PacsNotStorableException: null`` in ``dqr.log``
=================================================

Every retrieval cycle, a few minutes apart, logs this exception, and the PACS sees no incoming connection.

**Cause.** The name is misleading: it is not about the PACS. Each queued retrieval names XNAT's receiver as
``<AE title>:<port>``, and XNAT stores it only if a registered DICOM receiver matches that string exactly. The AE
title and port come from ``xnat.web.dicomAet`` and ``xnat.web.dicomPort`` (defaults ``XNAT`` and ``8104``).
Releases before the DICOM and web ports were split could send ``XNAT:8080``, the web port, which matches nothing.

**Check.** Compare the destination the requests carry with the receivers XNAT has:

.. code-block:: shell

   kubectl exec -n flip-trust trust-release-flip-trust-xnat-db-0 -- psql -U xnat -d xnat -c \
     "SELECT DISTINCT destination_ae_title FROM xhbm_queued_pacs_request;"
   kubectl exec -n flip-trust "$XNAT_POD" -- \
     curl -s -u admin:<admin-pass> http://localhost:8080/xapi/dicomscp/

**Fix.**

#. Make ``xnat.web.dicomAet`` and ``xnat.web.dicomPort`` match the receiver, and deploy.
#. Requests already queued keep their old destination. Correct them in place; XNAT retries failed requests by
   itself:

   .. code-block:: shell

      kubectl exec -n flip-trust trust-release-flip-trust-xnat-db-0 -- psql -U xnat -d xnat -c \
        "UPDATE xhbm_queued_pacs_request SET destination_ae_title = 'XNAT:8104'
         WHERE destination_ae_title = 'XNAT:8080';"

.. _ts-k8s-unassigned:

Studies arrive but land in the Unassigned prearchive
====================================================

Retrieval works (the PACS sends, requests reach ``RECEIVED``), but no sessions appear in the project.
``dqr.log`` shows:

.. code-block:: text

   WARN ... PacsDequeueThread - Cannot build session. 0 prearchive sessions found
   for study 1.2.826... in project <imaging-project-id>.

and ``/data/xnat/prearchive/`` fills with top-level timestamp folders instead of ``<project>/<timestamp>/``.

**Cause.** XNAT's DICOM receiver is the stock one. Routing studies to the requesting project, and relabelling them
the way the FL client looks them up, needs the retrieval plugin's identifier on the receiver.

**Fix.** The chart's ``xnat-init`` job configures the right receiver and the site anonymisation script on every
install and upgrade, so redeploy (or run ``make -C trust/deploy/helm xnat-init``). To set it by hand, delete the
stock receiver and create this one:

.. code-block:: shell

   kubectl exec -n flip-trust "$XNAT_POD" -- \
     curl -s -X DELETE -u admin:<admin-pass> http://localhost:8080/xapi/dicomscp/<id>
   kubectl exec -n flip-trust "$XNAT_POD" -- \
     curl -s -X POST -u admin:<admin-pass> -H "Content-Type: application/json" \
     http://localhost:8080/xapi/dicomscp -d '{
       "aeTitle": "XNAT", "port": 8104, "enabled": true,
       "customProcessing": true, "directArchive": true,
       "identifier": "dqrObjectIdentifier", "anonymizationEnabled": true,
       "whitelistEnabled": false, "routingExpressionsEnabled": false
     }'

Studies already stranded in the Unassigned prearchive were stored without the relabelling. Do not archive them by
hand: delete the top-level timestamp folders and re-pull them (:ref:`ts-k8s-force-repull`).

.. note::

   With direct archiving on, the ``Cannot build session`` warning still appears on every pull and is harmless:
   sessions skip the prearchive. Count the project's sessions with
   ``ls /data/xnat/archive/<imaging-project-id>/arc001 | wc -l`` in the pod.

.. _ts-k8s-xnat-401:

imaging-api gets 401 from XNAT
==============================

Project creation fails at once. trust-api logs
``External API error 401 for POST http://imaging-api:8000/projects/create-project-from-central-hub-project``, with
XNAT's *Your login attempt failed* page in the body.

**Cause.** XNAT's service account (``flipServiceAccount``) does not accept the password imaging-api sends. Older
charts created the account directly in XNAT's database with a fixed password. On XNAT 1.10 and later an account
created that way can never sign in, because it lacks XNAT's authentication record. Repeated failures also lock the
account for an hour.

**Check** whether the account has its authentication record:

.. code-block:: shell

   kubectl exec -n flip-trust trust-release-flip-trust-xnat-db-0 -- psql -U xnat -d xnat -c \
     "select xdat_username, auth_method from xhbm_xdat_user_auth;"

**Fix, when** ``flipServiceAccount`` **is listed:** set its password to the chart Secret's
``xnat-service-password`` through XNAT's API. The ``xnat-init`` job does the same on every deploy.

.. code-block:: shell

   kubectl exec -n flip-trust "$XNAT_POD" -- \
     curl -s -X PUT -u admin:<admin-pass> -H "Content-Type: application/json" \
     http://localhost:8080/xapi/users/flipServiceAccount \
     -d '{"password": "<xnat-service-password from the chart Secret>"}'

**Fix, when it is not listed:** no password change can repair it. Delete the account and deploy again; the
``xnat-init`` job recreates it properly:

.. code-block:: shell

   kubectl exec -n flip-trust "$XNAT_POD" -- \
     curl -s -X DELETE -u admin:<admin-pass> \
     "http://localhost:8080/xapi/users/flipServiceAccount?permanently=true"
   make -C trust/deploy/helm deploy-trust-k8s KIT=<CODE> PROD=<env>

.. _ts-k8s-dcm2niix-not-registered:

No NIfTI is produced: the dcm2niix command was never registered
================================================================

Studies import and archive, but conversion never runs. ``GET /xapi/commands?name=dcm2niix`` returns ``[]``, while
the ``xnat-init`` job reported success. Current charts fail the job instead, with
``does not hold the ContainerManager role``.

**Cause.** XNAT's container service needs the admin account to hold the ``ContainerManager`` role before it will
register the command. Older charts did not grant it and hid the failure.

**Fix.** Deploy the current chart, which grants the role and registers the command. On a cluster set up by an older
chart, grant the role by hand and deploy again:

.. code-block:: shell

   kubectl exec -n flip-trust "$XNAT_POD" -- \
     curl -s -X PUT -u admin:<admin-pass> -H "accept: application/json" \
     http://localhost:8080/xapi/users/admin/roles/ContainerManager

Then ``/xapi/users/admin/roles/`` must include ``ContainerManager``, and ``/xapi/commands?name=dcm2niix`` must list
a command with a ``dcm2niix-scan`` wrapper.

.. _ts-k8s-dcm2niix-no-nifti:

dcm2niix runs but fails: ``Unable to find any DICOM images in /input``
======================================================================

``GET /xapi/containers`` shows conversion containers with status ``Failed``; their log reads
``Error: Unable to find any DICOM images in /input (or subfolders 5 deep)``.

**Cause.** XNAT's container service was not told which volume holds the archive, so it mounted the scan folder
from the node's own disk, where it is empty.

**Fix.** The chart registers the container-service backend with the release's XNAT volume. If you configure the
backend by hand, set:

.. code-block:: json

   "combined-pvc-name": "<release>-flip-trust-xnat-web",
   "combined-path-translation": "/data/xnat/"

**Keep the trailing slash** on the path: without it Kubernetes rejects every conversion job, and only
``logs/containers.log`` shows the error. Restart ``xnat-web`` after changing the backend.

.. note::

   The conversion job mounts the same volume as ``xnat-web``. With the default ``ReadWriteOnce`` volume that only
   works when the job runs on the node running ``xnat-web``. On a multi-node cluster, give the XNAT data volume a
   ``ReadWriteMany`` storage class (``xnat.web.persistence.accessMode`` and ``storageClassName``).

.. _ts-k8s-dcm2niix-stale-image:

``XNAT has 0 'dcm2niix-scan' wrappers on the pinned ...``
=========================================================

Creating a project fails with imaging-api naming both images, for example:

.. code-block:: text

   XNAT has 0 'dcm2niix-scan' wrappers on the pinned
   'ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724' (registered images:
   xnat/dcm2niix:latest), not one, so project ... gets no DICOM->NIfTI trigger. ...

**Cause.** XNAT keeps the conversion command in its own database, bound to the image it was first configured with.
An XNAT set up before the image was pinned keeps converting with the old one, and looks healthy.

**Check** which image each command uses:

.. code-block:: shell

   kubectl exec -n flip-trust "$XNAT_POD" -- \
     curl -s -u admin:<admin-pass> http://localhost:8080/xapi/commands | jq -r '.[] | "\(.name) -> \(.image)"'

**Fix.** Deploy again (or run ``make -C trust/deploy/helm xnat-init``). The init job moves the existing command to
the pinned image in place, so every project's conversion trigger keeps working. On Compose, see
:ref:`ts-compose-dcm2niix`.

.. _ts-k8s-dcm2niix-jobs-cap:

dcm2niix jobs swamp the node, or wait for a slot
================================================

**Before the conversion cap (FLIP#1448):** during a cohort pull the namespace fills with Pending pods that are not
the release's own, one per archived scan, each requesting a whole CPU. The node sits near 100% of its CPU requests
and nothing else schedules: a restarted trust pod stays Pending, and on AKS ``az aks command invoke`` fails because
its own pod cannot start.

**Cause.** Each conversion job requested a whole CPU, and they all started at once. The chart now caps them
(``xnat.containerService.maxConcurrentJobs``, see "Container Service jobs (dcm2niix)" in the
:gh:`chart README <trust/deploy/helm/README.md#container-service-jobs-dcm2niix>`). The cap applies only to jobs
created after the ``helm upgrade`` that re-registered dcm2niix: a job keeps the pod spec it was created with, so a
backlog from before the upgrade still requests a CPU per pod.

**With the cap (expected, not a fault):** jobs with no pod yet, whose events read
``FailedCreate ... exceeded quota: <release>-flip-trust-xnat-container-jobs``. They start as running conversions
finish; the job controller retries each at most a minute apart.

**Check:**

.. code-block:: shell

   # Conversions running against the cap
   kubectl describe resourcequota -n flip-trust -l app.kubernetes.io/component=xnat-container-jobs
   # The registration carries no resource settings (both print null)
   kubectl exec -n flip-trust "$XNAT_POD" -c tomcat -- sh -c \
     'curl -s -u "$XNAT_SERVICE_USER:$XNAT_SERVICE_PASSWORD" "http://localhost:8080/xapi/commands?name=dcm2niix"' \
     | jq '.[0] | ."reserve-memory", ."limit-cpu"'
   # Container Service jobs created before the upgrade. Every one carries XNAT_* labels; an old one has a CPU
   # *limit* in its pod template (the 1-CPU request is added to the pod, not the job), so select on the limit.
   kubectl get jobs -n flip-trust -l XNAT_PROJECT -o json | jq -r '.items[]
     | select(.spec.template.spec.containers[0].resources.limits.cpu != null) | .metadata.name'

**Fix.** If the registration still shows ``1000`` / ``1``, run ``helm upgrade`` (it re-runs the ``xnat-init`` hook).
Then delete the jobs the last command lists (``... | xargs kubectl delete job -n flip-trust``). The scans they were
for stay unconverted, so relaunch dcm2niix on them (:ref:`ts-k8s-nifti-backlog`); the cap paces them.
``make -C trust/deploy/helm selftest`` checks that a conversion completes.

.. _ts-k8s-nifti-backlog:

Some archived scans have no NIfTI
=================================

A project's sessions hold DICOM, but some scans have no NIfTI, so training sees fewer samples than the cohort has.
New scans convert normally.

**Cause.** Conversion runs once, when a scan is archived. Scans archived while the trigger was missing or broken,
or whose conversion was lost, are never converted afterwards.

**Fix.** ``reconvert_nifti`` in the imaging-api pod lists every archived scan with DICOM and no NIfTI, and with
``--launch`` converts them through XNAT's bulk launcher. Run it on an idle trust (empty retrieval queue); it skips
sessions archived in the last 10 minutes.

.. code-block:: shell

   # Report only: each FLIP project's scans without NIfTI, and what would be launched
   kubectl exec -n flip-trust deploy/trust-release-flip-trust-imaging-api -- \
     python -m imaging_api.scripts.reconvert_nifti
   # Convert one project (hub project id or XNAT project ID) and follow the conversions
   kubectl exec -n flip-trust deploy/trust-release-flip-trust-imaging-api -- \
     python -m imaging_api.scripts.reconvert_nifti --project <id> --launch

How it behaves:

- **Which projects.** Without ``--project``, ``--launch`` converts only projects whose trigger is active, meaning
  the hub's *DICOM to NIfTI* setting is on. A named project is converted whatever its trigger says.
- **No double conversions.** It never launches on a scan whose conversion has not finished. A conversion that will
  never finish holds its scan back until you pass ``--relaunch-unfinished``.
- **Followed to the result** for ``--wait`` minutes (default 30; ``0`` returns at once). Each scan is reported as
  converted, converting, without a container yet, or ended without NIfTI.
- **Retries are re-runs.** Run ``--launch`` again to retry the failures. A scan is left alone after
  ``--max-attempts`` (default 3) of its conversions have ended.
- **One failure does not stop the rest.** A project XNAT will not list is reported ``NOT READ`` and the others
  continue.

.. list-table:: Exit codes
   :header-rows: 1
   :widths: 10 90

   * - Code
     - Meaning
   * - 0
     - Every launch converted, or nothing was needed.
   * - 1
     - A project was not read, a launch failed, or a conversion ended without NIfTI.
   * - 2
     - Unknown project id. Nothing was launched.
   * - 3
     - The wait ended first. Large backlogs queue behind the cluster's capacity; run the report again later.

On Compose the same tool is ``make -C trust reconvert-nifti`` (:ref:`ts-compose-nifti-backlog`). The XNAT UI can
launch the same conversion by hand from the project's *Run Containers* bulk launcher.

.. _ts-k8s-force-repull:

Status stays on "Processing": forcing a re-pull
===============================================

The project page shows the Trust on "Processing" indefinitely, and the re-import logs
``No studies to retry import for project ...``.

**Cause.** imaging-api counts a study as *Processing* whenever XNAT holds an executed request for it, whatever that
request's status, and re-imports only studies counted as *Failed* or *QueueFailed*.

**Fix.** Delete the executed requests for the imaging project (child table first), then re-import:

.. code-block:: shell

   kubectl exec -n flip-trust trust-release-flip-trust-xnat-db-0 -- psql -U xnat -d xnat -c "
   DELETE FROM xhbm_executed_pacs_request_series_ids WHERE executed_pacs_request IN
     (SELECT id FROM xhbm_executed_pacs_request WHERE xnat_project='<imaging-project-id>');
   DELETE FROM xhbm_executed_pacs_request WHERE xnat_project='<imaging-project-id>';"

Then trigger the re-import from the project page. imaging-api logs ``All studies queued successfully`` and sessions
are archived a few minutes later.

.. _ts-k8s-pacs-render:

The chart refuses to render with a real PACS
============================================

With ``pacs.host`` set to the Trust's PACS, ``helm`` stops with a message starting ``pacs.host is <host> but …``.
Each message names its fix:

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Message says
     - Fix
   * - ``xnat.web.dicomService.type is ClusterIP``
     - Expose the DICOM receiver: ``NodePort`` with ``xnat.web.dicomNodePort`` (equal to ``xnat.web.dicomPort``), or
       ``LoadBalancer``.
   * - ``xnat.web.service.type is NodePort`` (or ``LoadBalancer``)
     - That Service is now the web console only. Move the value to ``xnat.web.dicomService.type`` and set
       ``xnat.web.service.type`` back to ``ClusterIP``. Reach the console with ``kubectl port-forward``.
   * - ``xnat.web.dicomNodePort is unset``
     - Set it, equal to ``xnat.web.dicomPort``, widening the API server's node-port range if needed.
   * - ``allowedIngressCIDRsWithPorts contains 0.0.0.0/0``
     - Scope the rule to the PACS's own address. Never open the DICOM port to every address.
   * - ``no egress rule reaches its query/retrieve port``
     - Add the PACS to ``networkPolicies.allowedEgressCIDRsWithPorts``.

A trust that worked around the old single Service with its own hand-made Service and NetworkPolicy must delete them
once the chart manages DICOM exposure. Helm does not own them, so it neither adopts nor removes them:

.. code-block:: shell

   kubectl delete svc xnat-web-dicom-external -n flip-trust --ignore-not-found
   kubectl delete networkpolicy <release>-allow-ingress-cidrs -n flip-trust --ignore-not-found
   kubectl get svc,networkpolicy -n flip-trust -o name | grep -iE 'dicom|ingress'   # only the chart's are left

.. seealso::

   "Connect a real PACS" on :doc:`../deploy-flip/deploy-flip-node-kubernetes`, and
   :doc:`../components/component-pacs` for the values and network rules.

**************
OMOP data
**************

.. _ts-k8s-cohort-empty:

Cohort queries return no patients and every pod is healthy
==========================================================

**Cause.** The OMOP vocabulary was never loaded, or only partly.

**Fix.** Check whether the vocabulary load ran: see "Load the OMOP core vocabulary" on
:doc:`../deploy-flip/deploy-flip-node-kubernetes`. If a seed job was killed part-way through the DICOM vocabulary
(out of memory, evicted, a cancelled upgrade), the database can report the vocabulary as loaded while it is not, and
every later upgrade skips it. Force one reload: set ``trustData.seed.forceDicomVocab: true`` in your values file
(``VALUES_FILE``, see "Key values" on :doc:`../deploy-flip/deploy-flip-node-kubernetes`) and deploy:

.. code-block:: shell

   make -C trust/deploy/helm deploy-trust-k8s KIT=<CODE> PROD=<env> VALUES_FILE=<your values file>

.. important::

   Set it back to ``false`` afterwards and deploy again. It reloads on every upgrade, and each reload duplicates rows in
   ``concept_relationship``.

.. _ts-k8s-query-failed:

Every cohort query fails with ``query_failed``
==============================================

data-access-api answers ``500 {"detail": "query_failed"}`` and logs
``FATAL: password authentication failed for user "data_analyst_reader"``. Unqualified table names may also fail
with ``The table 'person' does not exist.``

**Cause.** The OMOP data was restored from a backup that already held the read-only role with a different
password, and lacked the database's ``search_path``. Current charts reset both at every omop-db start (look for
``postStart`` in the omop-db pod's log).

**Fix on a live cluster,** without restarting omop-db:

.. code-block:: shell

   kubectl exec -n flip-trust trust-release-flip-trust-omop-db-0 -- \
     psql -U postgres -d trustomopdb -c \
     "ALTER ROLE data_analyst_reader WITH PASSWORD '<data-access-postgres-password from the Secret>';
      ALTER DATABASE trustomopdb SET search_path = omop, public;"
   kubectl rollout restart deployment/trust-release-flip-trust-data-access-api -n flip-trust

data-access-api keeps pooled connections, so it needs the restart to see the new ``search_path``.

.. _ts-k8s-trust-seed:

The ``trust-seed`` job fails
============================

The job that loads the mock data is kept when it fails, so its log is readable:

.. code-block:: shell

   kubectl logs -n flip-trust job/trust-release-flip-trust-trust-seed

- **No egress.** The job needs ``huggingface.co``, ``github.com`` / ``raw.githubusercontent.com`` and PyPI (see
  :gh:`trust/deploy/helm/NETWORK-POLICY.md`).
- ``ModuleNotFoundError: No module named 'psycopg'``: ``trustData.seed.sourceRef`` names a FLIP ref whose data
  loaders predate a database-driver fix. Set it in your values file to a newer ref, ideally the release tag your
  images were built from (the chart default, ``develop``, is a moving branch tip), and deploy again with
  ``VALUES_FILE=<your values file>``.

Re-running the deploy is safe: it replaces only the listed projects' rows.

*********************
Hub connection and FL
*********************

.. _ts-k8s-heartbeat-401:

trust-api heartbeats fail with 401
==================================

.. code-block:: shell

   kubectl logs -n flip-trust deploy/trust-release-flip-trust-trust-api --tail=20

- ``Not authenticated: API key is missing.``: the API-key **header name** is wrong, not the key.
  ``TRUST_API_KEY_HEADER`` must match the hub's (the platform default is ``Authorization``, which the chart uses).
- **Key rejected:** the Secret holds a key other than the registered one. Re-sync the kit:

  .. code-block:: shell

     make -C trust/deploy/helm sync-kit KIT=<CODE> PROD=<env>

- **No heartbeat at all:** check that ``CENTRAL_HUB_API_URL`` is right and that the egress policy lets trust-api
  reach it on 443.

.. _ts-k8s-fl-port-blocked:

The FL client gets ``Connection refused [Errno 111]`` connecting to the FL server
=================================================================================

Other pods in the namespace can reach the FL server address.

**Cause.** The egress policy does not open the FL server's port. By default it allows only DNS, HTTP and HTTPS;
``sync-kit`` adds the kit's FL port (``FL_EDGE_PORT``, else ``FL_SERVER_PORT``) to ``networkPolicies.allowedEgressPorts``.

**Check:**

.. code-block:: shell

   kubectl get networkpolicy -n flip-trust trust-release-flip-trust-egress -o yaml | grep -n port

**Fix.** Regenerate the override from the kit and deploy:

.. code-block:: shell

   make -C trust/deploy/helm deploy-trust-k8s KIT=<CODE> PROD=<env>

If you manage the values by hand, add the port to ``networkPolicies.allowedEgressPorts``.

.. seealso::

   "FL client won't connect" in the :gh:`chart README <trust/deploy/helm/README.md>` for the kit path, kit owner
   and GPU checks.

**********
Governance
**********

.. _ts-k8s-governance:

A service will not start after a governance edit
================================================

data-access-api crash-loops, or the NVIDIA FLARE FL client sits in ``Init:Error`` / ``Init:CrashLoopBackOff`` on its
``governance-extract`` init container. Both refuse an invalid document, and the reason is in the container log:

.. code-block:: shell

   kubectl logs -n flip-trust deploy/trust-release-flip-trust-data-access-api --tail=20
   kubectl logs -n flip-trust deploy/trust-release-flip-trust-fl-client-<net> -c governance-extract --tail=20
   # the FL client prints: [site-privacy] FATAL: <what is wrong, and where>

**Usual causes:** a key or section the format does not define, a misspelt action, a project id that is not a UUID,
a rule without an ``effect``, a ``min_cohort_size`` below the kit's floor, ``gamma <= 0`` or ``percentile`` outside
``[0, 100]``, or the site privacy filter set both in ``[fl_privacy.nvflare]`` and in ``FL_SITE_PRIVACY_*``. A
silently dropped rule would be worse than none, because the operator would believe it in force.

**Fix.** Validate the document, then deploy (``sync-kit`` also refuses an invalid one):

.. code-block:: shell

   make -C trust check-governance KIT=<CODE> PROD=<env>
   make -C trust/deploy/helm deploy-trust-k8s KIT=<CODE> PROD=<env>

.. _ts-k8s-governance-ignored:

The governance document is right, but the pods behave as if it were absent
==========================================================================

Look at what the pods actually hold:

.. code-block:: shell

   kubectl logs -n flip-trust deploy/trust-release-flip-trust-data-access-api | grep '\[governance\]'
   kubectl exec -n flip-trust deploy/trust-release-flip-trust-data-access-api -- cat /app/governance.toml | head -5

The file in the pod is the policy actually enforced.

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - What you see
     - Cause and fix
   * - ``[governance]`` line says ``no policy configured``
     - The release has no document. One set with ``--set-file`` is dropped by the next ``deploy-trust-k8s`` or
       ``upgrade-trust-k8s``: set ``ACCESS_POLICY_FILE`` in the kit and deploy through those targets.
       ``helm get values trust-release -n flip-trust`` shows what the release holds.
   * - No ``[governance]`` line at all
     - The image predates governance support and ignores the document. Move the trust to a release that has it.
   * - The line's ``sha256=`` differs from the ConfigMap's
     - The pod runs an older document. Compare against the ConfigMap, not the file on the deploy host (embedding can
       change trailing newlines): pipe
       ``kubectl get configmap trust-release-flip-trust-governance -n flip-trust -o jsonpath='{.data.governance\.toml}'``
       into ``sha256sum``. Editing the ConfigMap by hand restarts nothing; deploy with ``deploy-trust-k8s KIT=``.
   * - The FL client ignores ``[fl_privacy.nvflare]``
     - On Flower nothing reads that section. On NVIDIA FLARE the client reads only its extract:
       ``/app/governance/governance.fl_privacy.toml`` in the FL client pod.

.. seealso::

   The "Trust governance policy" section of the :gh:`chart README <trust/deploy/helm/README.md>`.

*******************
Diagnostic tools
*******************

.. _ts-k8s-dicom-ports:

DICOM addresses
===============

Defaults for the mocked Orthanc; a real PACS overrides them through the values named.

.. list-table::
   :header-rows: 1
   :widths: 22 22 26 12 18

   * - Service
     - AE title
     - Host
     - Port
     - Role
   * - XNAT receiver
     - ``XNAT`` (``xnat.web.dicomAet``)
     - ``xnat-web-dicom``
     - 8104 (``xnat.web.dicomPort``)
     - Receives the studies the PACS sends after a C-MOVE
   * - PACS
     - ``ORTHANC`` (``pacs.aeTitle``)
     - ``orthanc`` (``pacs.host``)
     - 4242 (``pacs.qrPort``)
     - Answers C-FIND and C-MOVE

XNAT queries the PACS under the receiver's own AE title, so there is no separate AE for imaging-api. The receiver's
expected settings in XNAT's database are ``ae_title=XNAT, port=8104, direct_archive=t, custom_processing=t,
identifier=dqrObjectIdentifier``:

.. code-block:: shell

   kubectl exec -n flip-trust trust-release-flip-trust-xnat-db-0 -- psql -U xnat -d xnat -c \
     "SELECT id, ae_title, port, direct_archive, custom_processing, identifier FROM xhbm_dicomscpinstance;"

Fix a wrong receiver through XNAT's API (:ref:`ts-k8s-unassigned`), not with SQL: XNAT caches the receiver, so a
database-only change needs a restart.

Manual DICOM tests
==================

The chart ships no DICOM test pod. Start a throwaway pod from any image with DCMTK, then:

.. code-block:: shell

   kubectl exec -n flip-trust <dcmtk-pod> -- echoscu orthanc 4242           # PACS
   kubectl exec -n flip-trust <dcmtk-pod> -- echoscu xnat-web-dicom 8104    # XNAT receiver
   # Find studies on Orthanc
   kubectl exec -n flip-trust <dcmtk-pod> -- findscu -aet DCMTK -aec ORTHANC orthanc 4242 \
     -k "QueryRetrieveLevel=STUDY" -k "StudyInstanceUID=" -k "AccessionNumber="
   # Send one study to XNAT
   kubectl exec -n flip-trust <dcmtk-pod> -- movescu -aet DCMTK -aec ORTHANC -aem XNAT orthanc 4242 \
     -k "QueryRetrieveLevel=STUDY" -k "StudyInstanceUID=<uid>"

Aim C-ECHO at ``xnat-web-dicom``, not ``xnat-web``, which serves the web console only. A passing C-ECHO does not
prove imports work (:ref:`ts-k8s-cstore-aborts`); ``make -C trust/deploy/helm smoke-cstore`` does.
