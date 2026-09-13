#####
FAQs
#####

* Can a user have more than 1 role assigned to them?
    In practice, no. The Admin Area assigns a single role — ``admin``, ``researcher`` or ``viewer`` — and changing a user's role replaces the one they hold rather than adding to it. There is no need to stack roles to broaden access: ``admin`` already includes all ``researcher`` capabilities. Should a user ever hold more than one role, their permissions are simply the union of the permissions their roles grant. To change a user's role, an administrator selects the new role in the Admin Area's User Management page; see :ref:`admin-project-and-user-management`.