#####
FAQs
#####

* Can a user have more than 1 role assigned to them?
    No. Roles are exclusive — each user holds exactly one of ``admin``, ``researcher`` or ``observer``. The role grants the user's effective permissions directly: ``admin`` already includes all ``researcher`` capabilities, so there is no need to stack roles to broaden access. To change a user's role, an administrator selects the new role in the Admin Area's User Management page; see :ref:`admin-project-and-user-management`.