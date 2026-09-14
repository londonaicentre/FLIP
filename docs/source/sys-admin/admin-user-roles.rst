.. _rbac-roles:

###########
User Roles
###########

.. note::

   Roles are **exclusive**: each user holds exactly one of ``admin``, ``researcher`` or ``viewer``. The ``admin`` role already includes every ``researcher`` capability, so administrators do not need additional role grants to perform researcher actions.

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
   * - ``viewer``
     - Provides read-only access to assigned projects. Viewers can view project details, cohort query results, model metrics and training results, and download model results. Viewers cannot create or edit projects, run or save cohort queries, create or edit models, upload model files, or initiate training.

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
     - Viewer
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

   Viewers have no explicit permissions. Their read-only access to projects is granted through project user access records (i.e., being added to a project by its owner or an admin).

.. note::

   ``ProjectUserAccess`` membership grants different write capabilities depending on the user's role: a Researcher member may contribute their own models on the project, while a Viewer member retains read-only access. Project-level writes (editing, staging, or deleting the project itself) remain restricted to the project owner and admins regardless of membership.

.. warning::

   Project ownership is not revoked by a role change. A user who created a project keeps project-level write access to it (editing, staging, deleting, and submitting cohort queries) even after being demoted to Viewer — ownership, not the current role, is the authority for owned projects. Demotion still removes the user's ability to create new projects or write to projects they do not own. To fully revoke a former owner's access to a project they own, transfer ownership to another user or delete the project.

Who can see whose profile
-------------------------

User profile visibility follows *Manage users*, not project ownership:

.. list-table::
   :header-rows: 1
   :widths: 40 20 20 20

   * - Can read
     - Admin
     - Researcher
     - Viewer
   * - Any user's full profile (name, organisation, account status)
     - Yes
     - No
     - No
   * - Their own full profile
     - Yes
     - Yes
     - Yes
   * - Whether a given email address is registered, and its user ID
     - Yes
     - Yes
     - No

Only *Manage users* grants the full profile of another user. Every authenticated user can read their
own profile regardless of role.

Researchers additionally need to resolve an email address when adding someone to a project, so they
may check whether an address is registered. That check returns only the user's ID, email address and
enabled flag — never their name or organisation — and users cannot be *listed* without *Manage
users*: no endpoint will hand a Researcher the roster.

.. warning::

   The address check is not yet bounded per caller, so users can still be *enumerated* even though
   they cannot be listed. A Researcher who guesses addresses — an NHS address space is fairly
   predictable — learns which of them hold accounts, one address per request, and nothing caps how
   many they may try. What the check discloses stays narrow (ID, email address, enabled flag); how
   often it may be asked does not. Rate-limiting it per authenticated caller is tracked in
   `FLIP#961 <https://github.com/londonaicentre/FLIP/issues/961>`_.

.. note::

   Unlike project-level writes, this capability follows the *Create projects* permission rather than
   project ownership. A project owner who has been demoted to Viewer keeps their existing project
   write access (see the ownership warning further up the page) but loses the ability to look up new
   members, because the add-member form is not shown to Viewers.
