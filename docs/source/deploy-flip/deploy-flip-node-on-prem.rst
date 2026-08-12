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
itself. ``sudo`` is required: the provisioning playbook deliberately does
**not** add the login user to the ``docker`` group (docker group membership is
root-equivalent — any member can mount ``/`` into a container and chroot in),
so all docker-touching commands on the trust host run via ``sudo``:

.. code-block:: shell

   cd ../../..
   sudo -E env PROD=<stag|true> make -C trust up-trust KIT=<CODE>   # the trust code you scaffolded

Then verify the trust is polling: ``sudo docker logs -f trust-api`` should
show successful task polls against the Central Hub.

******************************************
Onboarding an on-prem trust (step by step)
******************************************

When the Central Hub is already deployed, an on-prem trust is onboarded
asynchronously: the FLIP admin (who holds prod AWS creds) scaffolds,
registers, and packages a kit; the operator (who does not) extracts it and
brings the stack up. There is no SSH path — each side runs its own step
locally. ``<CODE>`` is the trust's short code; the kit file is
``trust/.env.<CODE>.production``.

**1. Scaffold the kit (FLIP admin).** On a workstation with prod AWS creds:

.. code-block:: shell

   make new-trust TRUST_CODE=<CODE> TRUST_NAME="<trust name>" PROD=true

This writes ``trust/.env.<CODE>.production`` from the base template
(``trust/.env.example``): a host-local profile, mock-stack default
credentials, and ``<run-make-register-trust>`` placeholders for the
hub-managed blocks.

**2. Register the kit on the prod hub (FLIP admin).**

.. code-block:: shell

   make register-trust KIT=<CODE> PROD=true

This registers the trust on the prod hub and fills **both** managed blocks in
one step: the Kit credentials (``TRUST_API_KEY``,
``TRUST_INTERNAL_SERVICE_KEY``, ``FL_KIT_SLOT``, ``FL_KIT_SLOT_NUMBER``,
``EXPECTED_TRUST_ID``) and the Hub-shared block (12 keys). The hub stores only
SHA-256 hashes of the credentials and cannot re-emit them, so this is a
write-once operation — re-register to rotate.

**3. Package the kit (FLIP admin).** Tarball the populated kit file + the
operator's slice of the FL participant kit:

.. code-block:: shell

   make -C deploy/providers/AWS package-onprem-trust-kit KIT=<CODE> PROD=true

The script reads ``FL_BACKEND`` / ``FL_KIT_SLOT`` out of
``trust/.env.<CODE>.production`` (it does not edit the file), slices only this
operator's portion of the FL participant kit out of S3
(``net-1/services/<slot>/`` for nvflare; ``net-1/certificates/`` + one
``supernode_credentials_<N>`` for flower), and writes a tarball under
``deploy/providers/AWS/build/trust-kits/``. Send it to the operator over an
encrypted channel — it contains a plaintext API key, AES encryption key, and
an FL TLS private key.

**4. Extract, configure, and start (trust operator).** Extract the tarball,
copy the kit into the checkout, edit **only** the Host-local profile, then
bring the stack up:

.. code-block:: shell

   tar -xzf flip-trust-kit-<slot>-<date>.tar.gz
   cp .env.<CODE>.production trust/
   # Edit trust/.env.<CODE>.production (Host-local profile section only):
   #   - FL_KIT_DIR=<path to extracted fl-kit>
   #   - adjust ports / bind-mount dirs to match your host
   #   - rotate the Trust-local passwords
   sudo -E make onboard-onprem-trust KIT=<CODE> PROD=true   # readiness checklist
   sudo -E make up-onprem-trust KIT=<CODE> PROD=true         # comes up after all checks pass

Both run via ``sudo``: the provisioning playbook deliberately does not add the
login user to the ``docker`` group (membership is root-equivalent), and the
checklist's docker-swarm row needs the docker socket.

``make onboard-onprem-trust`` prints a ✅/❌ checklist (your public IP, docker
swarm state, Hub-shared / Kit credentials populated, FL_KIT_DIR set and on
disk, FL kit contents present) — fix any ❌ rows before ``up-onprem-trust``,
which gates on the same checks. The checklist is read-only, so you can run it
at any point — including before the kit is fully staged — just to read off the
``Your public IP`` row and report it to the FLIP admin for step 5.

The prod trust compose mounts the extracted ``net-1/`` hierarchy into the
fl-client container, so preserve it as extracted.

**5. Open the AWS firewall (FLIP admin).** Once the operator reports their
host's public IP, open the FL-server NLB to it:

.. code-block:: shell

   make -C deploy/providers/AWS allow-local-trust-nlb LOCAL_TRUST_IP=<public-ip> PROD=true

``allow-local-trust-nlb`` runs a normal ``terraform plan``/``apply`` — the IPs
are real config, so later full applies stay idempotent (no drift).

Then verify the trust is polling: ``sudo docker logs -f trust-api`` should
show successful task polls against the Central Hub.

**Rotation (later, no re-mint).** When the admin rotates a Hub-shared value
(``AES_KEY_BASE64``, image tags, ``FL_BACKEND``), refresh only the Hub-shared
block from the admin's local env file — credentials are preserved:

.. code-block:: shell

   make sync-trust-kit KIT=<CODE> PROD=true

Re-transmit the refreshed kit to the operator over the same encrypted channel.

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
   its ``api_key_hash`` — via ``make register-trust KIT=<CODE>`` or the
   Add-Trust admin flow (``POST /admin/trusts``).
2. ``make register-trust KIT=<CODE>`` mints the trust's API key and internal
   service key into the kit file (``trust/.env.<CODE>.production`` for the
   on-prem trust) as part of registration.
3. No hub redeploy is needed — the trust registry is the live database, read
   on every request.

In the step-by-step flow above, the trust must be registered (step 2) before
the operator brings the stack up.

***********************
Network requirements
***********************

**No inbound port forwarding is needed.** Trusts poll the hub outbound for
tasks, and FL clients connect outbound to the FL server via the NLB. All
communication is trust-initiated.

The trust host must be able to make outbound connections to two **separate** Central Hub
endpoints — different hostnames behind different load balancers, so both must be allowlisted:

- The Central Hub FLIP API over HTTPS (port 443) — the ALB (e.g. ``app.flip.aicentre.co.uk``).
- The FL Server endpoint over gRPC or HTTP (port set by ``FL_SERVER_PORT``,
  default 8002) — the NLB (e.g. ``fl.app.flip.aicentre.co.uk``).

If the trust's public IP changes (common with residential broadband), update
the NLB security group. Add the new IP to the ``LOCAL_TRUST_PUBLIC_IPS`` HCL
list in the hub env file (``.env.stag`` / ``.env.production``), then apply
the targeted security-group change:

.. code-block:: shell

   make -C deploy/providers/AWS allow-local-trust-nlb LOCAL_TRUST_IP=<new-ip> PROD=<stag|true>

***************
Troubleshooting
***************

+----------------------------------+------------------------------------------------------------+
| Symptom                          | Check                                                      |
+==================================+============================================================+
| Trust not polling hub            | Trust stack running? ``sudo docker ps`` on the trust host. |
|                                  | Check ``trust-api`` logs for polling errors.               |
+----------------------------------+------------------------------------------------------------+
| ``Connection timed out`` (FL)    | Trust's public IP changed? Update the NLB security group.  |
|                                  | Host/router firewall blocking outbound on port 8002?       |
+----------------------------------+------------------------------------------------------------+
| Firewall blocking outbound       | Confirm the host/router firewall allows outbound HTTPS     |
|                                  | (443) and the FL gRPC port (``FL_SERVER_PORT``, def. 8002).|
+----------------------------------+------------------------------------------------------------+
| Ansible ``Permission denied``    | SSH key correct? User has ``sudo``? ``ANSIBLE_BECOME_PASS``|
|                                  | set if running in local-host mode?                         |
+----------------------------------+------------------------------------------------------------+
