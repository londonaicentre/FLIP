.. _admin-project-and-user-management:

##########
FLIP Admin
##########

.. warning:: User must be assigned the ``admin`` role.

********
Projects
********

FLIP Admins are responsible for un-staging projects and for recording the Project Approval decision at every Trust that has no Trust Admin.

Project Un-staging
==================

Model developers must *stage* a project for approval in order to progress to training of models against the cohort defined within the project. Once a project has been staged for approval, the project will be locked and no further amendments to the project or cohort query can be made.

If a project or a project's model needs to be amended i.e., at the request of a model developer or as an outcome of the approval process, the model developer will need to liaise with a FLIP Admin to un-stage the project and re-enable editing.

1. Navigate to the project list
2. Select specific project and navigate to the project page
3. Click the 'Unstage Project' button

.. figure:: ../assets/generated/gifs/flip/unstage-project.gif
   :align: center

   Un-staging a project.


Project Approval
================

Once the offline project approval process has been completed, the outcome at each Trust can be recorded: every Trust the project was staged for is approved or declined, each in its own time. FLIP records each decision with who made it, when, and whether it was made by the hub or by the Trust itself, and writes it to the project's audit trail.

**Who decides a Trust.** A Trust with no Trust Admin is decided by a FLIP Admin, from the project page. A Trust with at least one Trust Admin decides for itself: only its Trust Admins approve or decline for it, from their **My Trust** page (see :ref:`trust-admin-guide`), and on the project page a FLIP Admin sees that Trust's row read-only. Appointing a Trust's first Trust Admin (see :ref:`assigning-a-trust-admin`) hands its decisions to the Trust; removing its last hands them back to the hub.

Approving a project allows the next stage of model training to commence and triggers the image retrieval process at each approved Trust.

.. warning::

   The model developer will not be able to initiate training at Trusts that have not approved the project, whether they declined or have not decided yet.

1. Navigate to the project page
2. Navigate to the 'Trust Approval' section
3. For a Trust you decide, click **Approve** or **Decline**
4. Click the 'Save Trust Decisions' button, which is enabled once at least one decision differs from what is already saved. Only the Trusts you changed are saved.

**Trusts decide at their own pace.** The project is approved as soon as one Trust approves it; the others can still approve or decline later. A Trust that approves after the project is approved gets its image retrieval then, and joins the project's existing models, taking part from their next training run. Once the project is approved, a Trust that has approved or declined cannot change its decision. If every Trust is declined, the project stays staged: either change a decision and save again, or un-stage the project so the model developer can amend it and stage it again. The earlier decisions stay in the project's audit trail.

For example, the below shows a project staged at one Trust being approved:

.. figure:: ../assets/generated/gifs/flip/approve-project.gif
   :align: center

   Approving a project.

**********
Admin Area
**********

The Admin Area enables certain functions, such as user management, configuration of deployment mode and the site banner, etc., available only to users with the ``admin`` role. To access this page, click the 'Admin' button on the left-hand side in the navigation menu.

User Management
===============

The User Management area facilitates:

- Review of access requests submitted from the login page
- Registration of new users
- Assignment of a user's role
- Disabling and re-enabling of user accounts
- Resetting of user passwords

.. note::

   The Admin Area assigns **one role per user**: selecting a role replaces the one currently held. See :ref:`rbac-roles` for the available roles and the permissions each grants.

Access Requests
^^^^^^^^^^^^^^^

Prospective users can ask for an account from the login page without holding one (see :ref:`request-access`). Each request records the requester's email address, full name and stated reason, and FLIP emails the platform's administrator address to announce it. The request is stored before that email is attempted, so it survives a mail backend that is unavailable or throttled — the 'Access Requests' queue, not the inbox, is the record to work from.

1. Click 'Access Requests' in the Admin Area's side navigation
2. Filter by status — 'Pending' (the default), 'Enrolled' or 'Dismissed'
3. On a pending request, click 'Enroll' to open the registration form pre-filled with the requester's name and email address (you choose the role), or 'Dismiss' to decline it

Enrolling or dismissing a request records which administrator handled it, and the request is retained under its new status rather than deleted.

Register User
^^^^^^^^^^^^^

1. Click the 'Register User' button
2. Enter the new user's email address and select a single role (``admin``, ``researcher`` or ``viewer``)
3. Click the 'Register User' button
4. The user will be emailed a one-time password to use on their :ref:`initial-login`

.. figure:: ../assets/generated/gifs/admin/create-user.gif
   :align: center

   Registering a new user.

Disable/Enable User
^^^^^^^^^^^^^^^^^^^

FLIP does not facilitate the deletion of user accounts, but rather enables accounts to be disabled in order to revoke access (and re-enabled to return access).

1. Select the user from the user list
2. Click the '...' button
3. Select the option to 'Disable User'

.. note::

   Disabled accounts may be re-enabled in a similar fashion.

.. figure:: ../assets/generated/gifs/admin/user-enable-disable.gif
   :align: center

   Enabling a user.

Manage Role
^^^^^^^^^^^

.. note::

   A user's role may be re-assigned at any time. The radio list assigns a single role, replacing the one currently held.

1. Select the user from the user list
2. Choose the new role from the radio list (``admin``, ``researcher``, ``viewer`` or ``trust admin``)
3. Click the 'Save User' button

.. figure:: ../assets/generated/gifs/admin/role-assignment.gif
   :align: center

   Re-assigning a user's role.

.. _assigning-a-trust-admin:

Assigning a Trust Admin
^^^^^^^^^^^^^^^^^^^^^^^

A Trust Admin approves or declines projects for **one** Trust, on that Trust's behalf, and otherwise works as a ``researcher`` (see :ref:`rbac-roles`). Appoint one when a Trust wants to decide on its own projects rather than relay its decisions to the hub.

1. Select the user from the user list (or register them first)
2. Choose ``trust admin`` from the radio list
3. In the 'Administers' dropdown inside the card, choose the Trust
4. Click the 'Save User' button, which stays disabled until a Trust is chosen

From then on, only that Trust's Trust Admins decide its projects — including any it has not decided yet — and the hub no longer can. To hand a Trust's decisions back to the hub, move its last Trust Admin to another role. Each change is written to the Trust's audit log. A user can administer one Trust; a Trust can have several Trust Admins.

.. figure:: ../assets/generated/gifs/admin/assign-trust-admin.gif
   :align: center

   Making a user the Trust Admin of one Trust.

Reset Password
^^^^^^^^^^^^^^

.. note::

  Users are able to reset their password themselves via the :ref:`forgot-password` functionality on the Login page.

1. Select the user from the user list
2. Click the '...' button
3. Click the 'Reset Password' button

.. figure:: ../assets/generated/gifs/admin/reset-password.gif
   :align: center

   Resetting a user's password.

Reset User MFA
^^^^^^^^^^^^^^

.. note::

  Use this when a user has lost their authenticator device and needs to enrol a new one. The reset clears the user's TOTP preference and revokes any active sessions, so on the next sign-in they are routed through the MFA enrolment page to register a new authenticator.

1. Select the user from the user list
2. Click the '...' button
3. Click the 'Reset MFA' button and confirm

.. figure:: ../assets/generated/gifs/admin/reset-mfa.gif
   :align: center

   Resetting a user's MFA.

.. warning::

  You cannot use this flow to recover **your own** MFA — the Admin Area requires an authenticated session, which you cannot obtain without your authenticator. If you are an administrator who has lost access to your TOTP device, see the "Cognito MFA Administration" section in ``deploy/README.md`` for the AWS CLI self-recovery runbook (local Keycloak stacks: clear the OTP credential in the Keycloak admin console).

Site Banner
===========

.. note::

   The site banner may be enabled or disabled at any time.

The site banner allows:

- A message to be set which is visible to all users of FLIP
- A link to be provided so that when a user clicks the site banner they will navigate to the specified URL


.. figure:: ../assets/generated/gifs/admin/site-banner.gif
   :align: center

   Editing the site banner.

Deployment Mode
===============

.. note::

   Deployment Mode can be enabled and disabled at any time, and the Site and User Management functions are still available while Deployment Mode is enabled.

Deployment Mode will disable all core functions of the FLIP Platform, and is intended for use while deployment or maintenance is occurring.

Deployment Mode also quiesces federated training so the Central Hub can be redeployed without killing an in-flight run:

- The FL scheduler stops picking up queued jobs. Queued jobs are not lost — they stay queued and resume, oldest first, once Deployment Mode is disabled.
- A training run already in flight is allowed to finish normally (its results upload and final status are unaffected); it is only the start of *new* jobs that is paused.
- Requests to initiate training are rejected with ``503 Service Unavailable`` while the mode is enabled.

This means Deployment Mode can be enabled at any time — even mid-training — ahead of a planned redeploy: enable it, wait for the current run (if any) to reach a terminal status, redeploy, then disable it. The Central Hub deploy command prints a reminder of this workflow (see ``deploy-centralhub`` in the AWS deployment README), and the ``GET /fl/quiesce`` endpoint reports whether the platform is quiesced (``deployment_mode`` plus ``fl_quiesced``, which is true when no net's scheduler is busy).

.. figure:: ../assets/generated/gifs/admin/deployment-mode.gif
   :align: center

   Enabling deployment mode.
