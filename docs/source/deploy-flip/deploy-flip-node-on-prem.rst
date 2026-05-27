.. _deploy-flip-node-on-prem:

###############################
Deploy a FLIP node on-prem
###############################

An on-prem FLIP node runs the trust-side stack (trust-api, imaging-api,
data-access-api, FL client, optional XNAT/Orthanc) on an Ubuntu host owned by
the Trust. The node polls the Central Hub for tasks over HTTPS — all
communication is outbound, no inbound ports are opened. This is the deployment
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

- Python 3.12+ and `UV <https://docs.astral.sh/uv/guides/install-python/>`_.
- Ansible (installed automatically by ``uv sync`` inside ``deploy/providers/AWS/``).
- Terraform outputs available — you must have already run ``make init`` and
  ``make apply`` in ``deploy/providers/AWS/`` (the Central Hub deployment).
- SSH access to the trust host, if provisioning remotely.

**Trust host** — an Ubuntu 22.04+ machine (physical or VM) with:

- A user account with ``sudo`` privileges (default: ``ubuntu``).
- SSH access from the operator workstation (if remote), or local access.
- Internet connectivity (to pull Docker images and packages).
- A writable directory for the FLIP application (default ``/opt/flip``).

**Central Hub deployed in AWS** — required so the trust can resolve the hub
URL, fetch the FL participant kit from S3, and so the operator can update the
NLB security group with the trust's public IP. See :doc:`deploy-central-hub`.

************************************
Recommended end-to-end (hybrid) flow
************************************

The wrapper target ``full-deploy-hybrid`` performs the full Central Hub deploy,
registers the trusts on the hub (``register-trusts``), and provisions the
on-prem trust:

.. code-block:: shell

   cd deploy/providers/AWS
   make full-deploy-hybrid PROD=<stag|true> [LOCAL_TRUST_IP=<public-ip>]

If ``LOCAL_TRUST_IP`` is omitted, the operator workstation's public IP is
auto-detected via ``curl -s https://api.ipify.org``. ``PROD`` is inherited
from the environment and supports both staging (``stag``) and production
(``true``).

After the wrapper exits, you still need to start the trust stack on the host
itself:

.. code-block:: shell

   cd trust
   env PROD=<stag|true> make up-local-trust

Then verify the trust is polling: ``docker logs -f trust-api`` should show
successful task polls against the Central Hub.

****************************************
Onboarding an on-prem trust (step by step)
****************************************

When the Central Hub is already deployed, an on-prem trust is onboarded
asynchronously: the trust operator provisions their own host, and the FLIP
admin opens the AWS firewall once the operator reports their public IP. There
is no SSH path — each side runs its own step locally.

**1. Register the trust (FLIP admin).** Register the trust on the hub — via the
Add-Trust modal in the UI or ``make register-trusts``. This mints the trust's
kit file ``trust/.env.<LOCAL_TRUST_NAME>`` — by default ``trust/.env.Trust_2``
in the FLIP prod environment, where Trust_2 is the on-prem (BDMS) slot
(``TRUST_API_KEY``,
``TRUST_INTERNAL_SERVICE_KEY``, ``FL_KIT_SLOT``, …).

**2. Distribute the kit (FLIP admin).** Send the kit file and the FL participant
kit to the trust operator out-of-band.

**3. Provision the host (trust operator).** On the trust host:

.. code-block:: bash

   cd deploy/providers/AWS
   read -rsp 'Sudo password: ' ANSIBLE_BECOME_PASS && echo
   export ANSIBLE_BECOME_PASS
   make provision-local-trust

In Fish, the prompt-and-export idiom is different:

.. code-block:: fish

   cd deploy/providers/AWS
   set -x ANSIBLE_BECOME_PASS (read -s -P 'Sudo password: ')
   make provision-local-trust

``provision-local-trust`` runs the Ansible playbook
``deploy/providers/local/site_local_trust.yml`` (installs Docker and system
packages, creates the ``/opt/flip/`` tree) and stages the FL participant kit.
It runs entirely on the trust host.

**4. Start the trust stack (trust operator).**

.. code-block:: shell

   cd trust
   env PROD=<stag|true> make up-local-trust

**5. Open the AWS firewall (FLIP admin).** Once the operator reports their
host's public IP, add it to ``LOCAL_TRUST_PUBLIC_IPS`` (an HCL list) in the env
file ``.env.<stag|production>``, then apply:

.. code-block:: shell

   # in .env.stag / .env.production
   LOCAL_TRUST_PUBLIC_IPS=["1.2.3.4"]

.. code-block:: shell

   cd deploy/providers/AWS
   make allow-local-trust-nlb LOCAL_TRUST_IP=<public-ip>

``allow-local-trust-nlb`` runs a normal ``terraform plan``/``apply`` — the IPs
are real config, so later full applies stay idempotent (no drift). Passing
``LOCAL_TRUST_IP`` makes it check that IP is in the list before applying.

Then verify the trust is polling: ``docker logs -f trust-api`` should show
successful task polls against the Central Hub.

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
| ``AES_KEY_BASE64``               | Symmetric key shared with the hub for encrypted        |
|                                  | payloads.                                              |
+----------------------------------+--------------------------------------------------------+
| ``TRUST_INTERNAL_SERVICE_KEY``   | Per-trust shared secret used inside the trust for      |
|                                  | calls between trust-api / imaging-api / fl-client and  |
|                                  | imaging-api / data-access-api. Never leaves the trust. |
+----------------------------------+--------------------------------------------------------+

**Hub-side prerequisites** (must already be in place before the trust can
connect):

1. The trust must be registered on the hub — a row in the ``trust`` table with
   its ``api_key_hash`` — via ``make register-trusts`` or the Add-Trust admin
   flow (``POST /admin/trusts``).
2. ``register_trust`` mints the trust's API key and internal service key into
   the kit file (``trust/.env.<LOCAL_TRUST_NAME>`` for the on-prem trust —
   typically ``trust/.env.Trust_2`` in the FLIP prod environment) as part of
   registration.
3. No hub redeploy is needed — the trust registry is the live database, read
   on every request.

The ``full-deploy-hybrid`` wrapper runs ``register-trusts`` automatically as
part of the deploy. In the step-by-step flow above, the trust must be
registered (step 1) before the operator provisions the host.

***********************
Network requirements
***********************

**No inbound port forwarding is needed.** Trusts poll the hub outbound for
tasks, and FL clients connect outbound to the FL server via the NLB. All
communication is trust-initiated.

The trust host must be able to make outbound connections to:

- The Central Hub FLIP API over HTTPS (port 443).
- The FL Server endpoint over gRPC or HTTP (configurable port; e.g. 8002).

If the trust's public IP changes (common with residential broadband), update
the NLB security group:

.. code-block:: shell

   TF_VAR_local_trust_public_ip=<new-ip> make -C deploy/providers/AWS plan apply

***************
Troubleshooting
***************

+----------------------------------+------------------------------------------------------------+
| Symptom                          | Check                                                      |
+==================================+============================================================+
| Trust not polling hub            | Trust stack running? ``docker ps`` on the trust host.      |
|                                  | Check ``trust-api`` logs for polling errors.               |
+----------------------------------+------------------------------------------------------------+
| ``Connection timed out`` (FL)    | Trust's public IP changed? Update the NLB security group.  |
|                                  | Host/router firewall blocking outbound on port 8002?       |
+----------------------------------+------------------------------------------------------------+
| Firewall blocking outbound       | Confirm the host/router firewall allows outbound HTTPS     |
|                                  | (443) and the FL gRPC port (default 8002).                 |
+----------------------------------+------------------------------------------------------------+
| Ansible ``Permission denied``    | SSH key correct? User has ``sudo``? ``ANSIBLE_BECOME_PASS``|
|                                  | set if running in local-host mode?                         |
+----------------------------------+------------------------------------------------------------+
