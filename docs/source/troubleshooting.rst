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

.. _troubleshooting:

###############
Troubleshooting
###############

These pages are organised by **where you see the problem**. Each entry's heading is the symptom, quoted as it
appears on screen or in a log where possible, followed by its cause and the fix.

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Page
     - Use it when
   * - :doc:`troubleshooting/fl-training-and-projects`
     - A project, image import or training run is stuck or fails, as seen in the FLIP UI. For researchers, Trust
       Admins and FLIP admins.
   * - :doc:`troubleshooting/central-hub-aws`
     - The Central Hub on AWS misbehaves: sign-in, deployments, FL status. For FLIP admins.
   * - :doc:`troubleshooting/trust-node-compose`
     - A trust node runs on Docker Compose: an on-prem host or the AWS-hosted trust. For trust operators.
   * - :doc:`troubleshooting/trust-node-kubernetes`
     - A trust node runs on Kubernetes with the Helm chart. For trust operators.

*************
Start here
*************

Three checks narrow most problems down to one component:

#. **Connection Status page.** Any signed-in user can see whether each Trust is online and which of its services
   is failing (:ref:`connection-status`).
#. **Hub health.** A FLIP admin runs the deployment check from the repo root:

   .. code-block:: shell

      make -C deploy/providers/AWS status HUB=<stag|prod>

#. **Trust health.** The trust operator checks the services on the trust host:

   .. code-block:: shell

      sudo docker ps                         # Compose: every service should be Up
      make -C trust/deploy/helm status       # Kubernetes: release status, plugin roster and smoke tests

.. seealso::

   Troubleshooting sections elsewhere in these docs:

   - :doc:`deploy-flip/deploy-local-dev` — the local development stack
   - :ref:`deploy-trust-node-network` — the network a trust host needs
   - :doc:`deploy-flip/deploy-flip-node-kubernetes` — the short list for a Kubernetes deployment
   - :ref:`enrichment-troubleshooting` — label upload before training
   - :doc:`components/component-logging-stack` — Grafana and Loki

.. toctree::
   :maxdepth: 2

   troubleshooting/fl-training-and-projects
   troubleshooting/central-hub-aws
   troubleshooting/trust-node-compose
   troubleshooting/trust-node-kubernetes
