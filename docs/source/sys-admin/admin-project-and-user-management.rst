.. _admin-project-and-user-management:

##########
FLIP Admin
##########

.. warning:: User must be assigned the ``admin`` role.

********
Projects
********

FLIP Admins are responsible for un-staging projects and completing the Project Approval process.

Project Un-staging
==================

Model developers must *stage* a project for approval in order to progress to training of models against the cohort defined within the project. Once a project has been staged for approval, the project will be locked and no further amendments to the project or cohort query can be made.

If a project or a project's model needs to be amended i.e., at the request of a model developer or as an outcome of the approval process, the model developer will need to liaise with a FLIP Admin to un-stage the project and re-enable editing.

1. Navigate to the project list
2. Select specific project and navigate to the project page
3. Click the 'Unstage Project' button

.. figure:: ../assets/flip/unstage-project.gif
   :width: 600
   :align: center

   Un-staging a project.


Project Approval
================

Once the offline project approval process has been completed, the outcome of approval at each Trust can be selected.

Approving a project allows the next stage of model training to commence and triggers the image retrieval process at each Trust.

.. warning::

   The model developer will not be able to initiate training at Trusts that have not approved to participate in the project and its model's training.

1. Navigate to the project page
2. Navigate to the 'Project Approval' section
3. For each Trust selected to be a potential participant in the project, toggle the switch to indicate their approvals, or lack thereof
4. Click the 'Save Trust Approvals' button

For example, the below reflects a case where only one Trust is marked as approved:

.. figure:: ../assets/flip/approve-project.gif
   :width: 600
   :align: center

   Approving a project.

**********
Admin Area
**********

The Admin Area enables certain functions, such as user management, configuration of deployment mode and the site banner, etc., available only to users with the ``admin`` role. To access this page, click the 'Admin' button on the left-hand side in the navigation menu.

User Management
===============

The User Management area facilitates:

- Registration of new users
- Assignment of user roles
- Disabling and re-enabling of user accounts
- Resetting of user passwords

Register User
^^^^^^^^^^^^^

1. Click the 'Register User' button
2. Enter the new user's email address and assigned roles e.g., ``admin`` and/or ``researcher``
3. Click the 'Register User' button
4. The user will be emailed a one-time password to use on their :ref:`initial-login`

.. figure:: ../assets/admin/create-user.gif
   :width: 600
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

.. figure:: ../assets/admin/user-enable-disable.gif
   :width: 600
   :align: center

   Enabling a user.

Manage Roles
^^^^^^^^^^^^

.. note::

   A user's roles may be re-assigned at any time.

1. Select the user from the user list
2. Add or remove the roles as appropriate
3. Click the 'Save User' button

.. figure:: ../assets/admin/role-assignment.gif
   :width: 600
   :align: center

   Re-assigning roles to a user.

Reset Password
^^^^^^^^^^^^^^

.. note::

  Users are able to reset their password themselves via the :ref:`forgot-password` functionality on the Login page.

1. Select the user from the user list
2. Click the '...' button
3. Click the 'Reset Password' button

.. figure:: ../assets/admin/reset-password.gif
   :width: 600
   :align: center

   Resetting a user's password.

Site Banner
===========

.. note::

   The site banner may be enabled or disabled at any time.

The site banner allows:

- A message to be set which is visible to all users of FLIP
- A link to be provided so that when a user clicks the site banner they will navigate to the specified URL


.. figure:: ../assets/admin/site-banner.gif
   :width: 600
   :align: center

   Editing the site banner.

Deployment Mode
===============

.. note::

   Deployment Mode can be enabled and disabled at any time, and the Site and User Management functions are still available while Deployment Mode is enabled.

Deployment Mode will disable all core functions of the FLIP Platform, and is intended for use while deployment or maintenance is occurring.

.. figure:: ../assets/admin/deployment-mode.gif
   :width: 600
   :align: center

   Enabling deployment mode.
