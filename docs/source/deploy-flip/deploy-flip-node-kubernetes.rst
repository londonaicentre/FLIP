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

.. _deploy-flip-node-kubernetes:

##################################
Deploy a FLIP node on Kubernetes
##################################

The ``flip-trust`` Helm chart deploys the trust node onto a Kubernetes cluster the trust already runs. Use it when
the trust's platform team operates Kubernetes rather than standalone hosts. This page is step 5 of
:doc:`onboarding-a-trust`.

As on every platform, **all traffic to the hub is outbound**. Every application Service is ``ClusterIP``; the one
optional inbound path is the DICOM receiver for a real PACS (see `Connect a real PACS`_).

.. admonition:: Before you start
   :class: note

   Do steps 1 to 4 of :doc:`onboarding-a-trust` first: you need the kit file and this trust's FL participant kit.
   When the node is deployed and its DICOM path verified, continue at step 6 there.

The chart is validated on single-node k3s: read `Known limitations`_, particularly storage and Pod Security, before
choosing it for a production trust. The :gh:`Helm chart README <trust/deploy/helm/README.md>` has the full values
and secrets reference.

.. contents:: On this page
   :local:
   :depth: 1

******************
What gets deployed
******************

.. list-table::
   :header-rows: 1
   :widths: 30 50 20

   * - Values block
     - Service
     - Keeps data?
   * - ``trustApi``
     - API gateway; polls the Central Hub
     - No
   * - ``imagingApi``
     - DICOM image retrieval
     - No
   * - ``dataAccessApi``
     - OMOP database queries (plus a small approved-cohort store)
     - Yes
   * - ``flClient``
     - FL participant (FLARE or Flower)
     - No
   * - ``omopDb``
     - OMOP PostgreSQL database
     - Yes
   * - ``orthanc``
     - DICOM PACS (mocked)
     - Yes
   * - ``xnat.web``, ``xnat.db``, ``xnat.nginx``
     - XNAT, its database and its reverse proxy
     - Yes (web, db)
   * - ``observability.loki``, ``.alloy``, ``.grafana``
     - Log storage, log collection (a DaemonSet) and dashboards
     - Yes (Loki, Grafana)

``omopDb`` can point at an external database instead: set ``omopDb.enabled: false`` and ``omopDb.external.host`` /
``.port``, and the chart creates an ``ExternalName`` Service in its place.

*************
Prerequisites
*************

The requirements every trust node shares (trust data, Central Hub, network) are on :doc:`deploy-trust-node`.

Deployment host
===============

The machine you run the ``make`` targets from. It needs a FLIP checkout and:

- ``kubectl`` 1.28 or newer, configured for the target cluster;
- ``helm`` 4.x (the version CI tests; 3.16 or newer also works);
- ``uv``, which runs the kit sync and upgrade scripts with the Python and PyYAML they declare;
- Python 3, for ``make status``;
- ``docker``, only for ``stage-kit`` on a kind cluster (see `Step 3 — Stage the FL participant kit`_).

Cluster
=======

- **Kubernetes 1.28 or newer** (EKS, AKS or on-prem).
- **A default StorageClass.** Without one, every PersistentVolumeClaim stays ``Pending``. Default sizes are below;
  change any with ``<block>.persistence.size`` and pick a class with ``<block>.persistence.storageClassName``.
- **A CNI that enforces NetworkPolicy** (Calico, Cilium, Antrea and similar). On one that ignores them, such as
  default flannel, the chart's policies render but do nothing.
- **RBAC** to create Secrets in the namespace (required) and to create the namespace itself, or have a cluster
  administrator create it first. The namespace is ``flip-trust`` unless you pass ``NAMESPACE=``.

.. list-table:: Default volume sizes
   :header-rows: 1
   :widths: 50 20 30

   * - Volume
     - Size
     - Survives uninstall?
   * - Shared images (imaging-api and FL client)
     - 100Gi
     - No
   * - XNAT archive
     - 100Gi
     - No
   * - Orthanc
     - 50Gi
     - No
   * - XNAT database
     - 20Gi
     - Yes
   * - OMOP database
     - 10Gi
     - Yes
   * - Loki
     - 10Gi
     - Yes
   * - Approved-cohort store (data-access-api)
     - 5Gi
     - No
   * - Grafana
     - 1Gi
     - No

Every volume is ``ReadWriteOnce`` by default. The FL client is pinned to imaging-api's node so the two can share the
images volume, and XNAT's DICOM-to-NIfTI conversion jobs mount the XNAT volume, so on a **multi-node** cluster see
`Known limitations`_.

GPUs
====

GPU training needs the `NVIDIA GPU Operator <https://github.com/NVIDIA/gpu-operator>`_ and an NVIDIA driver of
**580 or newer** on the GPU nodes, because the FL images ship PyTorch built for CUDA 13. The operator installs the
driver itself, so pin its ``driver.version`` to a 580+ release, or set ``driver.enabled=false`` and pre-install one on
the node. Then set ``flClient.gpu.enabled: true`` and ``flClient.gpu.count`` (see `Key values`_).

Extra network egress
====================

On top of the hub endpoints every node needs:

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Destination
     - Needed for
   * - FL server, the kit's FL port
     - ``sync-kit`` adds this port to the chart's egress policy for you.
   * - ``huggingface.co``, ``github.com``, ``raw.githubusercontent.com`` and PyPI
     - The mock-data seed job (``trustData.seed``), on install and on every upgrade while it is enabled.
   * - Your S3 vocabulary bucket
     - The OMOP vocabulary load job, only if ``omopDb.vocabLoad.s3Bucket`` is set.

To restrict egress further, see :gh:`NETWORK-POLICY.md <trust/deploy/helm/NETWORK-POLICY.md>`.

*****************************************************
Step 1 — Check out the release and copy in the kit
*****************************************************

*Who: trust operator.* Run this and every later command from the repo root of a FLIP checkout. ``<env>`` is the
``HUB`` value the kit was made for (``prod`` or ``stag``).

.. code-block:: shell

   git fetch --tags origin && git checkout v<X.Y.Z>   # the release the hub runs
   cp <received kit file> trust/

The chart templates come from the checkout, so never deploy from a branch tip. The chart never registers anything
itself: the kit file ``trust/.env.<CODE>.<env>`` is what the admin's registration produced.

**********************************
Step 2 — Generate the secrets file
**********************************

*Who: trust operator*

The kit holds the per-trust keys. The chart's other secrets (OMOP, XNAT, Orthanc and Grafana passwords) go in
``values-secrets.yaml``, generated from the kit:

.. code-block:: shell

   python3 trust/deploy/helm/scripts/generate_values.py \
     --env-file trust/.env.<CODE>.<env> \
     --output-dir trust/deploy/helm

This writes ``trust/deploy/helm/values-secrets.yaml`` (mode ``0600``). It builds ``orthanc-registered-users`` from
the kit's ``ORTHANC_USERNAME`` and ``ORTHANC_PASSWORD``, as Compose does; Orthanc will not start without it.

.. warning::

   ``values-secrets.yaml`` holds live trust credentials. It is gitignored; never commit it. The tracked
   ``values-secrets.yaml.example`` is only a field reference with every slot empty.

*************************************
Step 3 — Stage the FL participant kit
*************************************

*Who: trust operator*

**The chart never fetches the FL kit.** A trust holds no FLIP AWS credentials, so the kit must be on the node before
the FL client starts.

.. code-block:: shell

   make -C trust/deploy/helm stage-kit KIT_SRC=<slot-kit-dir> KUBE_CONTEXT=<kube-context>

- ``KIT_SRC`` is the slot's own kit: for FLARE the folder holding ``local/``, ``startup/`` and ``transfer/``; for
  Flower ``certificates/`` and ``keys/`` with this slot's credential only. The target reads the backend from that
  shape and refuses a folder with neither shape or both (``FL_BACKEND=nvflare|flower`` overrides).
- ``KUBE_CONTEXT`` is required, so the kit never lands in whichever cluster ``kubectl`` happens to point at.
- The kit goes to ``KIT_DEST`` on the node (default ``/opt/flip/fl-kit``), and the deploy targets mount that same
  path, whatever the kit's ``FL_KIT_DIR`` says. Any previous kit there is removed first. To use another path, pass
  the same ``KIT_DEST=<path>`` to ``stage-kit`` and to the deploy.

.. note::

   ``stage-kit`` copies into the node with ``docker cp``, which works for a **kind** cluster only. On any other
   cluster, place the kit on the node by whatever means your site allows, at ``/opt/flip/fl-kit`` (or the
   ``KIT_DEST`` you deploy with), readable by uid 1000 (FLARE) or 49999 (Flower).

If you skip this step, the FL client pod stays ``Pending`` with a ``hostPath type check failed`` event naming the
missing path.

On a managed cluster (AKS): the kit from a Secret
=================================================

A managed node pool replaces nodes at will, so nothing may live on one. Put the kit in a Secret instead and deploy
with ``flClient.kit.source=secret`` (FLIP#1390):

.. code-block:: shell

   make -C trust/deploy/helm kit-secret KIT_SRC=<slot-kit-dir> KUBE_CONTEXT=<kube-context>
   make -C trust/deploy/helm deploy-trust-k8s KIT=<CODE> HUB=<env> \
     PLATFORM_VALUES=values-aks.yaml KUBE_CONTEXT=<kube-context>

- ``values-aks.yaml`` carries what belongs to a managed cluster rather than to the trust: the kit from the Secret,
  Postgres data below the fresh disk's ``lost+found``, and the S3 vocabulary load off. ``PLATFORM_VALUES`` layers it
  after the per-trust override. Better, put ``K8S_PLATFORM_VALUES=values-aks.yaml`` in the kit file: every
  ``deploy``, ``deploy-trust-k8s`` and ``upgrade-trust-k8s`` with that ``KIT`` then applies it. A run without it
  moves PGDATA back to the volume root (both databases crash-loop) and looks for the kit on the node (the FL client
  stays ``Pending``).
- The overlays live beside the chart in the repository, not in the packaged chart, so a deploy from a chart tarball
  passes a checkout's copy by path.
- ``kit-secret`` makes one key per mounted file in ``<release>-flip-trust-fl-kit``. It refuses a kit missing its
  backend's files, a nested directory, or more than a Secret's 1 MiB, and a Flower kit holding anything beyond
  ``certificates/ca.crt`` and one ``keys/supernode_credentials_<slot>``, so a whole provisioning tree never lands
  in one trust's Secret. Set ``flClient.kit.secretName`` only for a Secret you build yourself with
  ``scripts/kit_secret.py --name``.
- At pod start the ``stage-fl-kit`` init container copies the files into the pod's own kit volume, because a Secret
  volume is read-only and NVFLARE writes into ``local/``, ``startup/`` and ``transfer/``. That volume lives as long
  as the pod, so anything NVFLARE leaves in ``transfer/`` goes with it.

***************
Step 4 — Deploy
***************

*Who: trust operator*

.. code-block:: shell

   make -C trust/deploy/helm deploy-trust-k8s KIT=<CODE> HUB=<env> [KUBE_CONTEXT=<ctx>]

This runs, in order:

#. **Override.** Regenerates ``trust/deploy/helm/k8s-trust-<CODE>.yaml`` from the kit: hub URL, FL backend, image
   tag, kit path and the FL server's egress port. The file holds no secrets and is rewritten on every deploy, so
   **do not edit it by hand**.
#. **Preflight.** Checks tools and versions, cluster access, a default StorageClass, the chart files and the kit, and
   runs ``helm lint``. A failed required check stops the deploy; a missing default StorageClass is only a warning.
#. **Install.** ``helm upgrade --install`` of release ``trust-release`` into namespace ``flip-trust``, with the
   chart's ``values.yaml``, ``values-secrets.yaml`` and the override, in that order.
#. **Keys.** Patches the kit's per-trust keys into the release Secret and restarts trust-api, imaging-api and
   data-access-api. This runs even if the install step failed, so trust-api never polls with a stale key; the
   command still exits with the install's status.

To check a host before deploying, run the preflight alone:
``make -C trust/deploy/helm preflight KIT=<CODE> HUB=<env>``.

.. note::

   The repo-root ``make deploy-trust-k8s`` and the chart's plain ``make -C trust/deploy/helm deploy`` install the
   chart **without** the per-trust override and without patching the keys. Use the ``deploy-trust-k8s KIT=<CODE>``
   form above for a registered trust.

The wait budget: ``HELM_TIMEOUT``
=================================

The deploy waits for the ``xnat-init`` job, which configures XNAT after install. ``HELM_TIMEOUT`` (default ``30m``)
bounds that wait, and the standalone ``make -C trust/deploy/helm xnat-init`` uses the same budget. Time one run on
your cluster (``kubectl get job -n flip-trust -w``), then raise it if needed:

.. code-block:: shell

   make -C trust/deploy/helm deploy-trust-k8s KIT=<CODE> HUB=<env> HELM_TIMEOUT=45m

.. warning::

   Set below the job's real duration, the deploy fails with ``resource Job/... not ready`` **after** Helm has already
   applied the new spec, so the error names Helm rather than the wait. The job itself keeps running in the cluster.

If you add ``--wait`` to your own ``helm upgrade``, note that Helm 4 waits until workloads are genuinely ready, so size
``--timeout`` for the slowest service rather than for the API call.

******************************
Step 5 — Verify the DICOM path
******************************

*Who: trust operator*

Polling (step 6 of :doc:`onboarding-a-trust`) does not prove imaging works: the two share no code. Run both checks;
neither implies the other.

.. code-block:: shell

   make -C trust/deploy/helm status
   make -C trust/deploy/helm smoke-cstore

``status``
   Shows the Helm release and runs the chart's health checks: pod readiness, volumes, HTTP endpoints, and the XNAT
   plugins. It fails when the plugin files in the running ``xnat-web`` pod differ from the release's
   ``xnat.web.plugins.urls`` (no upgrade has rolled ``xnat-web`` since the list changed), or when XNAT did not load
   one of them.

``smoke-cstore``
   Sends a real DICOM C-STORE from Orthanc to XNAT and checks XNAT received it. It needs an Orthanc that already
   holds an instance (``trustData.seed.orthanc: true``). Exit 0 is a pass and 1 a failure. An **inconclusive** result
   prints a yellow warning but still exits 0: re-run it with the PACS idle before trusting the path.

.. important::

   A DICOM C-ECHO is not a substitute. It never reaches XNAT's importer and passes even when every store is aborted.

The trust self-test
===================

``make -C trust/deploy/helm selftest KUBE_CONTEXT=<ctx>`` runs the acceptance checklist every trust shape shares
(the VM node self-test's, FLIP#1406) against the deployed release:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Check
     - Passes when
   * - release ready
     - Every Deployment, StatefulSet and DaemonSet of the release has finished rolling out (all replicas updated and
       ready, no old pod left).
   * - fl kit delivered
     - The FL client sees its kit (``/app/startup/fed_client.json``; Flower: ``/certs/ca.crt`` and ``/keys``).
   * - trust-api / imaging-api / data-access-api health
     - Each answers its own ``/health`` with ``"status":"ok"``.
   * - omop seeded
     - ``omop.person`` has rows.
   * - orthanc seeded
     - Orthanc holds instances.
   * - orthanc requires auth
     - Orthanc answers 401 without credentials.
   * - xnat c-store
     - ``smoke-cstore`` exits 0; its inconclusive exit 2 fails the check.
   * - dcm2niix conversion
     - The smallest Orthanc series, imported into a throwaway ``FLIPSELFTEST`` project, is converted by a dcm2niix
       job: its container ends ``Complete`` and the scan holds a ``.nii``/``.nii.gz`` file within 10 minutes
       (``--conversion-timeout``).
   * - fl client running
     - The FL client is running, with no restart, at two samples 45 s apart.

It needs no hub: an unreachable hub or FL server is not a failure. Every check runs in a pod the release already
has, so it adds no RBAC or NetworkPolicy exception. The conversion check starts one dcm2niix job, as a cohort pull
would, and deletes its ``FLIPSELFTEST`` project when it ends, pass or fail: a C-STORE alone shows XNAT received a
study, not that this cluster can convert it. If other conversions are queued, its job waits for a slot under the
cap (:ref:`ts-k8s-dcm2niix-jobs-cap`), so run it on an idle trust. It writes
``selftest-k8s-<backend>-<stamp>.json`` and ``.md`` (and ``latest-k8s-<backend>.md``) under ``SELFTEST_OUT``
(default ``build/selftest/`` in the checkout) and exits non-zero on any failure.

Continue at step 6 of :doc:`onboarding-a-trust`.

*****************************
Load the OMOP core vocabulary
*****************************

*Who: trust operator.* Why the vocabulary is needed, and how to export it, is step 7 of :doc:`onboarding-a-trust`.
After a default install, Helm prints a warning that it is not loaded. On Kubernetes you have two ways to load it:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - You have
     - Do this
   * - An S3 bucket holding the bundle that the cluster can read
     - Set ``AICENTRE_BUCKET_NAME`` in the kit to your own environment's bucket and re-run Step 4. ``sync-kit`` writes
       it into ``omopDb.vocabLoad.s3Bucket`` and a load job runs after each install or upgrade.
   * - Your own Athena / TRUD export
     - Leave ``omopDb.vocabLoad.s3Bucket`` empty and run the loader from :ref:`onprem-core-vocabulary` over a
       ``kubectl port-forward`` to the ``omop-db`` service.

.. warning::

   The S3 load runs as a Helm hook. If the cluster cannot read the bucket, the hook fails and **the whole release
   is marked failed**, not just the vocabulary load. The deploy does not roll it back.

On a cluster with no route to AWS, ``values-aks.yaml`` keeps the S3 load out whatever bucket the kit names, and with
it the metadata endpoint, which on Azure hands out managed-identity tokens; ``NO_VOCAB_LOAD=1`` does the same for
``sync-kit`` on any other cluster. The trust then runs without the vocabulary until one is loaded another way, and
cohort queries that name concepts through ``omop.concept`` return nothing. The x-ray tutorial ships
``query_novocab.sql`` for this, and ``make e2e_smoke`` on a dev stack (``HUB`` unset) smokes such a trust with it.
With ``HUB`` set, pass it by hand:
``QUERY_FILE=fl-tutorials/nvflare/image_classification/xray_classification/query_novocab.sql`` (Flower:
``fl-tutorials/flower/xray_classification/query_novocab.sql``).

.. seealso::

   The chart README, "OMOP core vocabulary" and "Local clusters (kind)", for the port-forward commands, AWS
   credentials for the load job, and loading the Synthea EHR cohort the same way.

**********
Key values
**********

Most values come from the kit through ``sync-kit``. Those you may set yourself are below. Keep them in a values file
of your own; the deploy targets pass ``-f $(VALUES_FILE)`` first, so ``VALUES_FILE=<your file>`` layers it over the
chart defaults, and the generated override still wins for the keys it sets. Do not pass settings with
``helm upgrade --set``: the next deploy does not reuse them, so they silently disappear.

.. list-table::
   :header-rows: 1
   :widths: 32 18 50

   * - Value
     - Default
     - What it does
   * - ``flClient.kitHostPath``
     - ``/opt/flip/fl-kit``
     - Path **on the node** holding the FL kit. Required whenever the FL client is enabled. The deploy targets set it
       from ``KIT_DEST``, over the kit's ``FL_KIT_DIR`` (see Step 3).
   * - ``flBackend``
     - from the kit
     - ``nvflare`` or ``flower``.
   * - ``global.image.tag``
     - from the kit
     - The release every FLIP-built image runs. Set from the kit's ``DOCKER_TAG``; ``upgrade-trust-k8s TAG=`` moves
       it.
   * - ``flClient.gpu.enabled`` / ``flClient.gpu.count``
     - ``false`` / ``0``
     - Request GPUs for the FL client. Needs the GPU Operator (see `GPUs`_).
   * - ``trustData.seed.enabled``
     - ``true``
     - Loads the **mock** projects into OMOP (and Orthanc) on install and every upgrade. **Turn it off** for a site
       whose stores hold real data only.
   * - ``trustData.seed.orthanc``
     - ``false``
     - Also load the mock DICOM studies into Orthanc (about 2 GB per trust). ``smoke-cstore`` needs it.
   * - ``omopDb.vocabLoad.s3Bucket``
     - ``""``
     - Bucket for the OMOP vocabulary load; empty skips it (see above).
   * - ``omopDb.enabled`` / ``omopDb.external.host``
     - ``true`` / ``""``
     - Use an external OMOP database instead of the in-cluster one.
   * - ``<block>.persistence.storageClassName`` / ``.size``
     - cluster default / see above
     - Storage class and size per volume (``sharedImagesPvc.*`` for the shared images volume).
   * - ``pacs.*``, ``xnat.web.dicomService.type``, ``xnat.web.dicomNodePort``
     - mocked Orthanc
     - Point XNAT at the trust's real PACS and expose its DICOM receiver (see `Connect a real PACS`_).
   * - ``networkPolicies.allowedEgressCIDRs`` / ``allowedEgressCIDRsWithPorts``
     - ``[]``
     - Extra egress rules, for example to a PACS or an external database.
   * - ``imagePullSecrets``
     - ``[]``
     - Registry credentials for private images.
   * - ``namespace.create``
     - ``false``
     - Let the chart create the namespace with Pod Security labels. ``make deploy`` creates the namespace itself,
       without those labels.

**Governance policy.** A trust's optional governance document is set through the kit, not as a value: point
``ACCESS_POLICY_FILE`` at it in the kit file, validate it with ``make -C trust check-governance KIT=<CODE>
HUB=<env>``, then re-run Step 4. The chart README's "Trust governance policy" section has the details.

Connect a real PACS
===================

By default XNAT retrieves from the chart's mocked Orthanc over the cluster network. For the trust's own PACS, set
``pacs.host``, ``pacs.aeTitle`` and ``pacs.qrPort``, expose the DICOM receiver with ``xnat.web.dicomService.type``
(``NodePort`` with ``xnat.web.dicomNodePort``, or ``LoadBalancer``), and open both directions in
``networkPolicies``. The chart refuses to render a real PACS without both the egress and the ingress rule, and keeps
the XNAT web console on ``ClusterIP``; reach it with ``kubectl port-forward``.

.. seealso::

   :doc:`../components/component-pacs`, "Exposing the DICOM Receiver", for the values and the network rules.

*********
Upgrading
*********

*Who: trust operator*

Check out the new release tag first, then:

.. code-block:: shell

   make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> HUB=<env> [TAG=vX.Y.Z] [KUBE_CONTEXT=<ctx>]

``TAG`` defaults to the release the hub runs. The command pins it in your kit, regenerates the override and Secret
from the kit, and runs ``helm upgrade``. Volumes survive and the API deployments roll onto the new images.

When the admin refreshes your kit's hub-shared settings, see "Later — rotate hub-shared settings" on
:doc:`onboarding-a-trust`.

.. warning::

   While ``trustData.seed.enabled`` is on, every upgrade re-applies the mock projects. Turn it off for a site whose
   stores hold real data only.

.. seealso::

   :doc:`/sys-admin/admin-upgrading-sites` for the full runbook, per-image pins and rolling back.

************
Uninstalling
************

*Who: trust operator*

.. code-block:: shell

   make -C trust/deploy/helm undeploy

This runs ``helm uninstall`` on the release (``make undeploy-trust-k8s`` from the repo root does the same). To see
what is installed: ``make -C trust/deploy/helm list``.

.. warning::

   Uninstalling **deletes data**. Helm removes the release's own volume claims, and the volumes go with them under
   the usual ``Delete`` reclaim policy: Orthanc's PACS store, XNAT's archive, the shared images volume, Grafana and
   data-access-api's approved-cohort store. Back these up first if the data matters.

What survives is the namespace, the FL kit staged on the node, and the StatefulSet volumes of the OMOP database,
XNAT database and Loki. A later deploy therefore comes back with the OMOP and XNAT databases but an empty PACS and
an empty XNAT archive, so the XNAT database points at files that no longer exist. Delete those StatefulSet volume
claims yourself for a clean slate.

***************
Troubleshooting
***************

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Symptom
     - Check
   * - trust-api logs ``401 "API key is missing"``
     - The API-key header name does not match the hub's. The chart default, ``Authorization``, is the platform
       default; change it only if your hub uses another header.
   * - FL client ``Pending`` with ``hostPath type check failed``
     - The FL kit is not on the node at ``flClient.kitHostPath``. Run Step 3, then deploy again.
   * - Flower SuperNode logs ``Permission denied`` on ``/certs`` or ``/keys``
     - The kit was staged for the wrong backend. Re-stage from the slot's own kit directory, or pass
       ``FL_BACKEND=flower``.
   * - PVCs ``Pending``
     - No default StorageClass, or none matching ``storageClassName``.
   * - Orthanc fails with ``CreateContainerConfigError``
     - ``orthanc-registered-users`` is empty in ``values-secrets.yaml``: the kit lacks ``ORTHANC_USERNAME`` or
       ``ORTHANC_PASSWORD`` (Step 2).
   * - Deploy refused with ``conflict with "kubectl-patch"``
     - Run ``make -C trust/deploy/helm patch-kit-secrets KIT=<CODE> HUB=<env>`` once, then deploy again. Do not use
       ``--force-conflicts``: it would overwrite the live keys.
   * - Cohort queries return no patients, every pod healthy
     - The OMOP vocabulary is not loaded (see above).
   * - Polling works, imaging pulls fail
     - Run ``status`` and ``smoke-cstore`` (Step 5).
   * - Hundreds of Pending pods that are not the release's own
     - Conversion jobs from before the dcm2niix cap: :ref:`ts-k8s-dcm2niix-jobs-cap`.

.. seealso::

   :ref:`ts-trust-node-kubernetes` for the full list.

*****************
Known limitations
*****************

- **Pod Security.** The API services and FL client run hardened, but ``xnat-web``, ``xnat-db``, ``omop-db`` and
  ``orthanc`` cannot yet run under the ``restricted`` Pod Security profile.
- **Multi-node clusters.** Volumes are ``ReadWriteOnce``. The FL client is pinned to imaging-api's node, and XNAT's
  conversion jobs must land on the ``xnat-web`` node. Across several nodes, give the XNAT volume a ``ReadWriteMany``
  storage class (``xnat.web.persistence.accessMode: ReadWriteMany``, for example NFS or EFS).
- **Orthanc** runs a single replica on an embedded SQLite database.
