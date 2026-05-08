.. _rbac-roles:

###########
User Roles
###########

.. list-table::
   :widths: 5 90
   :header-rows: 1

   * - Role
     - Description
   * - ``admin``
     - Grants all platform permissions including; project approval, unstaging projects, deleting any project, managing deployments (deployment mode), managing the site banner, user management, accessing the admin panel, and all ``researcher`` capabilities.
   * - ``researcher``
     - Allows a user to create and manage FLIP projects.

       - **On projects they own:** Researchers can edit project details, stage projects for approval on specified trusts, create cohort queries, create models, upload files required for those models, and initiate model training.
       - **On projects they have been added to as a member (via a** ``ProjectUserAccess`` **record) but do not own:** Researchers may contribute their own models — creating models, uploading files for those models, and initiating training — but they cannot edit, stage, or delete the project itself, nor modify models created by other Researchers.
   * - ``observer``
     - Provides read-only access to assigned projects. Observers can view project details, cohort query results, model metrics and training results, and download model results. Observers cannot create or edit projects, run or save cohort queries, create or edit models, upload model files, or initiate training.

***********
Permissions
***********

The following table summarises the permissions assigned to each role:

.. list-table::
   :widths: 40 10 10 10
   :header-rows: 1

   * - Permission
     - Admin
     - Researcher
     - Observer
   * - Access admin panel
     - Yes
     - No
     - No
   * - Approve projects
     - Yes
     - No
     - No
   * - Unstage projects
     - Yes
     - No
     - No
   * - Delete any project
     - Yes
     - No
     - No
   * - Manage deployments (deployment mode)
     - Yes
     - No
     - No
   * - Manage site banner
     - Yes
     - No
     - No
   * - Manage users
     - Yes
     - No
     - No
   * - Manage projects (create, edit, stage, train)
     - Yes
     - Yes
     - No

.. note::

   Observers have no explicit permissions. Their read-only access to projects is granted through project user access records (i.e., being added to a project by its owner or an admin).

.. note::

   ``ProjectUserAccess`` membership grants different write capabilities depending on the user's role: a Researcher member may contribute their own models on the project, while an Observer member retains read-only access. Project-level writes (editing, staging, or deleting the project itself) remain restricted to the project owner and admins regardless of membership.
