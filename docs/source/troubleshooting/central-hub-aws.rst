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

.. _ts-central-hub-aws:

######################
Central Hub on AWS
######################

Problems a FLIP admin meets running the Central Hub on AWS. Every command runs from the repo root on the deployment
workstation. ``HUB`` selects the environment (``stag`` or ``prod``).

Start with the deployment check. It covers resource health, connectivity, endpoints and containers:

.. code-block:: shell

   make -C deploy/providers/AWS status HUB=<env>

.. contents:: On this page
   :local:
   :depth: 2

.. seealso::

   Terraform state problems, CI and verifying which image a running task pulled are maintainer topics, kept in
   :gh:`deploy/providers/AWS/TROUBLESHOOTING.md`. Its section 5 also shows how to reach the hub database through
   the bastion.

*******************
AWS access
*******************

.. _ts-hub-sso-expired:

``Unable to locate credentials`` or ``AccessDenied`` on every ``aws`` command
==============================================================================

**Cause.** Your AWS SSO session expired.

**Fix.** Sign in again. The target picks the profile that matches ``HUB`` and uses a device code, so it works
over SSH too:

.. code-block:: shell

   make -C deploy/providers/AWS aws-login HUB=<env>

*******************
Sign-in
*******************

.. _ts-hub-login-srp:

Sign-in fails with "There was a problem logging you in" after a Terraform apply
===============================================================================

Credentials are correct (they work from the AWS CLI) and ``status`` is green. In the browser's DevTools, the
**Network** tab shows the POST to ``cognito-idp.<region>.amazonaws.com`` answered with ``400``:

.. code-block:: text

   x-amzn-ErrorType:    InvalidParameterException
   x-amzn-ErrorMessage: USER_PASSWORD_AUTH flow not enabled for this client

**Cause.** The Cognito app client and the UI bundle are out of step. The app client accepts only SRP sign-in, which
the current UI uses, but the bundle served from S3 is older. Terraform updates Cognito straight away, while the UI is
deployed separately and ``full-deploy`` does not rebuild it.

**Fix.** Rebuild and deploy the UI:

.. code-block:: shell

   make -C deploy/providers/AWS deploy-ui HUB=<env>

Wait one to three minutes for the CloudFront invalidation, then hard-refresh the sign-in page.

.. tip::

   After any apply that changes Cognito, deploy the UI too. The sign-in page shows the same generic message for
   every failure, so check the Network tab before assuming a wrong password.

*******************
FL networks
*******************

.. _ts-hub-fl-status-empty:

FL status is empty and flip-api logs ``Name or service not known`` for the FL API
=================================================================================

**Cause.** flip-api cannot resolve the FL API address in ``NET_ENDPOINTS``. On AWS, Terraform builds this value
from the service-discovery name (``fl-api-net-1.flip.local``); on a hub run with Docker Compose it must use the
Compose service name.

**Fix.**

- **On AWS:** apply Terraform and redeploy, so the hub runs with the generated value:

  .. code-block:: shell

     make -C deploy/providers/AWS apply HUB=<env>
     make -C deploy/providers/AWS deploy-centralhub HUB=<env>

- **On a Compose hub:** set the service name in the hub env file and restart flip-api:

  .. code-block:: text

     NET_ENDPOINTS={"net-1":"http://fl-api-net-1:8000"}

flip-api updates the stored endpoint from ``NET_ENDPOINTS`` every time it starts, so no database change is needed.

.. _ts-hub-invalid-signature:

fl-api or fl-server crash-loops with ``LoadResult.INVALID_SIGNATURE``
=====================================================================

The same fault can show at a Trust as a TLS failure: ``SSLV3_ALERT_BAD_CERTIFICATE`` or
``CERTIFICATE_VERIFY_FAILED``.

**Cause.** NVIDIA FLARE signs every file of a participant kit when it is provisioned. The signature breaks if a kit
file is edited afterwards, or if a copy mixes files from two provisioning runs (a fresh ``signature.json`` beside an
older ``rootCA.pem``).

**Fix.**

#. **Never edit a kit after provisioning.** To change a hostname, provision the kit again with the right value
   (:gh:`fl-services/nvflare/README.md`).
#. Copy the new kit into place. Both copies clear the destination first, so no stale file survives:

   .. code-block:: shell

      make -C deploy/providers/AWS apply HUB=<env>                     # hub: FL server and FL API storage
      make -C deploy/providers/AWS stage-fl-kit KIT=<CODE> HUB=<env>   # the AWS-hosted trust

.. warning::

   Do not use ``deploy-trust`` or ``full-deploy`` to recopy a kit on a live trust. They are first-install commands
   that re-seed the trust's data and wipe its XNAT archive (see :ref:`admin-upgrading-sites`).

.. _ts-hub-fl-clients-lost:

Trusts lose their FL connection after a hub deployment
======================================================

**Cause.** Deploying the hub restarts the FL server. NVIDIA FLARE clients reconnect on their own.

**Fix.** Usually none. If a Trust does not come back, see :ref:`ts-fl-target-unreachable`.
