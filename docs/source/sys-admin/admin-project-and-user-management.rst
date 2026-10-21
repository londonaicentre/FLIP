.. _admin-project-and-user-management:

##########
FLIP admin
##########

.. important:: Everything on this page needs the ``admin`` role.

FLIP admins unstage projects, record project approval at trusts that have no Trust Admin, and run the Admin Area:
users, the site banner and deployment mode.

.. contents:: On this page
   :local:
   :depth: 2

********
Projects
********

Project un-staging
==================

A model developer **stages** a project to ask for approval before training. Staging locks the project: neither the
project nor its cohort query can be changed.

If the project or one of its models needs changing (because the developer asks, or the approval process requires
it), the developer asks a FLIP admin to **unstage** it. That re-enables editing.

1. Open the project list.
2. Open the project.
3. Click **Unstage Project**.

.. figure:: ../assets/generated/gifs/flip/unstage-project.gif
   :align: center

   Un-staging a project.


Project approval
================

Approval itself happens offline. Once it is done, record the outcome in FLIP for each trust the project was staged
at. Each trust is approved or declined separately, in its own time. FLIP records who made each decision, when, and
whether it came from the hub or the trust, in the project's audit trail.

**Who decides a trust:**

- A trust with **no Trust Admin** is decided by a FLIP admin, on the project page.
- A trust with **at least one Trust Admin** decides for itself, on its **My Trust** page (see
  :ref:`trust-admin-guide`). On the project page, a FLIP admin sees that trust's row read-only.

Appointing a trust's first Trust Admin (see :ref:`assigning-a-trust-admin`) hands its decisions to the trust.
Removing its last Trust Admin hands them back to the hub.

**Approving** a project lets training start and triggers image retrieval at each approved trust.

.. warning::

   Model developers cannot start training at a trust that has not approved the project, whether it declined or has
   not decided yet.

To record decisions:

1. Open the project page.
2. Go to the **Trust Approval** section.
3. For each trust you decide, click **Approve** or **Decline**.
4. Click **Save Trust Decisions**. The button is enabled once a decision differs from what is saved, and only the
   trusts you changed are saved.

**Trusts decide at their own pace:**

- The project counts as approved as soon as one trust approves it. Other trusts can still approve or decline later.
- A trust that approves later gets its image retrieval then. It joins the project's existing models from their next
  training run.
- Once the project is approved, a trust that has approved or declined cannot change its decision.
- If every trust declines, the project stays staged. Either change a decision and save again, or unstage the project
  so the developer can amend and re-stage it. Earlier decisions stay in the audit trail.

The example below shows a project staged at one trust being approved:

.. figure:: ../assets/generated/gifs/flip/approve-project.gif
   :align: center

   Approving a project.

**********
Admin Area
**********

The Admin Area holds the functions only admins can use: user management, the site banner and deployment mode. To
open it, click **Admin** in the left-hand navigation menu.

User management
===============

From User Management you can:

- review access requests sent from the login page;
- register new users;
- assign a user's role;
- disable and re-enable accounts;
- reset passwords and MFA.

.. note::

   Each user has **one role**: selecting a role replaces the one they hold. See :ref:`rbac-roles` for the roles and
   what each can do.

Access requests
^^^^^^^^^^^^^^^

Anyone can ask for an account from the login page (see :ref:`request-access`). Each request records the email
address, full name and stated reason, and FLIP emails the platform's admin address about it.

.. tip::

   Work from the **Access Requests** queue, not the inbox. FLIP saves the request before it sends the email, so a
   request is kept even when the email fails.

1. Click **Access Requests** in the Admin Area's side navigation.
2. Filter by status: **Pending** (the default), **Enrolled** or **Dismissed**.
3. On a pending request, click **Enroll** or **Dismiss**. **Enroll** opens the registration form with the
   requester's name and email filled in; you choose the role.

FLIP records which admin handled each request. Handled requests are kept under their new status, not deleted.

Register user
^^^^^^^^^^^^^

1. Click **Register User**.
2. Enter the new user's email address and select one role (``admin``, ``researcher`` or ``viewer``).
3. Click **Register User**.

FLIP emails the user a one-time password for their :ref:`initial-login`.

.. figure:: ../assets/generated/gifs/admin/create-user.gif
   :align: center

   Registering a new user.

Disable/enable user
^^^^^^^^^^^^^^^^^^^

FLIP does not delete user accounts. To revoke access, disable the account; re-enable it to restore access.

1. Select the user in the user list.
2. Click **...**.
3. Select **Disable User**. Re-enabling works the same way.

.. figure:: ../assets/generated/gifs/admin/user-enable-disable.gif
   :align: center

   Enabling a user.

Manage role
^^^^^^^^^^^

You can change a user's role at any time. The new role replaces the old one.

1. Select the user in the user list.
2. Choose the new role (``admin``, ``researcher``, ``viewer`` or ``trust admin``).
3. Click **Save User**.

.. figure:: ../assets/generated/gifs/admin/role-assignment.gif
   :align: center

   Re-assigning a user's role.

.. _assigning-a-trust-admin:

Assigning a Trust Admin
^^^^^^^^^^^^^^^^^^^^^^^

A Trust Admin approves or declines projects for **one** trust, on its behalf, and otherwise works as a
``researcher`` (see :ref:`rbac-roles`). Appoint one when a trust wants to decide on its own projects rather than
pass its decisions to the hub.

1. Select the user in the user list (register them first if needed).
2. Choose ``trust admin``.
3. In the **Administers** dropdown inside the card, choose the trust.
4. Click **Save User**. The button stays disabled until a trust is chosen.

From then on, only that trust's Trust Admins decide its projects, including any not decided yet; the hub no longer
can. To hand the trust's decisions back to the hub, move its last Trust Admin to another role.

- A user can administer one trust; a trust can have several Trust Admins.
- Each change is written to the trust's audit log.

.. figure:: ../assets/generated/gifs/admin/assign-trust-admin.gif
   :align: center

   Making a user the Trust Admin of one Trust.

Reset password
^^^^^^^^^^^^^^

.. tip::

  Users can reset their own password with :ref:`forgot-password` on the login page.

1. Select the user in the user list.
2. Click **...**.
3. Click **Reset Password**.

.. figure:: ../assets/generated/gifs/admin/reset-password.gif
   :align: center

   Resetting a user's password.

Reset User MFA
^^^^^^^^^^^^^^

Use this when a user has lost their authenticator device. The reset clears their authenticator (TOTP) setting and
signs them out everywhere. At their next sign-in they are taken to the MFA enrolment page to register a new one.

1. Select the user in the user list.
2. Click **...**.
3. Click **Reset MFA** and confirm.

.. figure:: ../assets/generated/gifs/admin/reset-mfa.gif
   :align: center

   Resetting a user's MFA.

.. warning::

  You cannot reset **your own** MFA this way: the Admin Area needs a signed-in session, and you cannot sign in
  without your authenticator. Another operator with AWS access follows :ref:`deploy-central-hub-mfa-recovery`. On a local Keycloak stack, clear the OTP credential in the Keycloak
  admin console instead.

Site banner
===========

The site banner shows a message to every FLIP user. It can include a link that users follow by clicking the banner.
You can turn the banner on or off at any time.

.. figure:: ../assets/generated/gifs/admin/site-banner.gif
   :align: center

   Editing the site banner.

.. _deployment-mode:

Deployment mode
===============

**Deployment mode** turns off FLIP's core functions while a deployment or maintenance is under way. You can turn it
on or off at any time. Site banner and user management still work while it is on.

It also pauses federated training, so the Central Hub can be redeployed without killing a run:

- The FL scheduler stops picking up queued jobs. Queued jobs are kept and resume, oldest first, when deployment mode
  is turned off.
- A training run already under way finishes normally; its results upload and final status are unaffected. Only new
  jobs are held.
- Requests to start training are refused with ``503 Service Unavailable``.

Before a planned hub redeploy, even mid-training:

1. Turn deployment mode on.
2. Wait for the current run, if any, to finish.
3. Redeploy the hub.
4. Turn deployment mode off.

The hub deploy command (``make -C deploy/providers/AWS deploy-centralhub``) prints a reminder of these steps.

.. tip::

   ``GET /fl/quiesce`` on the hub API reports whether it is safe to redeploy: ``deployment_mode``, and
   ``fl_quiesced``, which is true when no net's scheduler is busy.

.. figure:: ../assets/generated/gifs/admin/deployment-mode.gif
   :align: center

   Enabling deployment mode.
