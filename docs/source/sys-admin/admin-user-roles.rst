.. _rbac-roles:

##########
User roles
##########

FLIP has four roles. In the Admin Area each user gets **one role**: choosing a new role replaces the one they hold
rather than adding to it. There is no need to stack roles, because ``admin`` already includes everything a
``researcher`` can do.

.. note::

   If a user does hold more than one role, their permissions are the union of what each role grants.

.. contents:: On this page
   :local:
   :depth: 1

*****
Roles
*****

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Role
     - What it allows
   * - ``admin``
     - Everything a ``researcher`` can do, plus: the admin panel, user management, deployment mode, the site banner,
       unstaging projects, deleting any project, and approving projects at every trust that has no Trust Admin.
   * - ``researcher``
     - Creating FLIP projects and working on them. What they can do depends on whether they own the project or are a
       member of it (see below).
   * - ``viewer``
     - Read-only access to the projects they have been added to.
   * - ``trust admin``
     - Everything a ``researcher`` can do, plus approving or declining projects for **one trust**, chosen when the
       role is assigned (see :ref:`assigning-a-trust-admin`).

Researchers
===========

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Project
     - What a researcher can do
   * - One they **own**
     - Edit the project, stage it for approval at chosen trusts, create cohort queries, create models, upload the
       models' files and start training.
   * - One they are a **member** of
     - Contribute their own models: create them, upload their files and start training. They cannot edit, stage or
       delete the project, or change other researchers' models.

Viewers
=======

Viewers can see project details, cohort query results, model metrics and training results, and download model
results. They cannot create or edit projects, run or save cohort queries, create or edit models, upload model files
or start training.

Viewers hold no permissions of their own. Their access comes from **project membership**: being added to a project by
its owner or an admin.

Trust Admins
============

- **Who decides a trust.** While a trust has at least one Trust Admin, only its Trust Admins approve or decline
  projects staged at it; an ``admin`` no longer can. A trust with no Trust Admin is decided by an ``admin``.
- **What they can see.** A Trust Admin can view any project staged at their trust, even one they are not a member of:
  its details, cohort query and results, its list of models and its imaging status. They cannot edit, stage or delete
  it, or add models to it. The models' own pages (metrics, logs, results) stay with the project's members.
- **Where their authority stops.** The approval right applies at their one trust only. They see nothing of projects
  staged only at other trusts and have no say over them.
- **Where they decide.** On the **My Trust** page (see :ref:`trust-admin-guide`).

Every change to who is a trust's Trust Admin is written to that trust's audit log.

***********
Permissions
***********

.. list-table::
   :widths: 40 15 15 15 15
   :header-rows: 1

   * - Permission
     - Admin
     - Researcher
     - Viewer
     - Trust Admin
   * - Access admin panel
     - Yes
     - No
     - No
     - No
   * - Approve or decline projects
     - At trusts with no Trust Admin
     - No
     - No
     - At their own trust
   * - View projects staged at their trust (read-only)
     - Yes
     - No
     - No
     - Yes
   * - Unstage projects
     - Yes
     - No
     - No
     - No
   * - Delete any project
     - Yes
     - No
     - No
     - No
   * - Manage deployments (deployment mode)
     - Yes
     - No
     - No
     - No
   * - Manage site banner
     - Yes
     - No
     - No
     - No
   * - Manage users
     - Yes
     - No
     - No
     - No
   * - Create projects; manage projects they own (edit, stage, delete, train); contribute models and train on projects
       they are a member of
     - Yes
     - Yes
     - No
     - Yes
   * - Manage all projects, whoever owns them
     - Yes
     - No
     - No
     - No

Project membership gives different rights by role: a Researcher member can contribute models, a Viewer member can
only read. Editing, staging or deleting the project itself stays with the owner and admins, whatever the membership.

.. warning::

   **A role change does not remove project ownership.** A user who created a project keeps write access to it
   (editing, staging, deleting, submitting cohort queries) even after being demoted to Viewer. Demotion only stops
   them creating new projects or writing to projects they do not own.

   To fully remove a former owner's access, transfer the project to another user or delete it.

*************************
Who can see whose profile
*************************

Profile visibility follows the *Manage users* permission, not project ownership.

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

Researchers need to look up an email address when adding someone to a project. The lookup returns only the user's ID,
email address and whether the account is enabled, never their name or organisation. Without *Manage users*, nobody
can list the users.

The lookup follows the *Create projects* permission, not ownership. An owner demoted to Viewer keeps write access to
their projects but can no longer add members, because Viewers are not shown the add-member form.

.. warning::

   **Accounts can still be enumerated.** The address lookup is not rate-limited per user. A Researcher who guesses
   addresses (NHS addresses are fairly predictable) can learn which ones have accounts, one address per request,
   with no cap on attempts. Each answer reveals only the ID, address and enabled flag.
