check_governance
================

.. py:module:: check_governance

.. autoapi-nested-parse::

   Validate a trust's governance document without starting the services (FLIP#1259).

   Backs ``make -C trust check-governance KIT=<CODE>`` and the on-prem onboarding checklist.
   The services fail closed on an invalid policy, so an operator editing rules wants the error
   here rather than from a container that then refuses to come back up.

   Deliberately calls the same ``load_policy`` the service calls at startup: a separate
   validator would be free to drift from what is actually enforced, and an operator would
   then be told a document is fine when the service would reject it. It prints the same
   digest the service logs at startup, so the operator can match the two.

   Stdlib-only, like the ``data_access_api.policy`` package it imports: it runs on the trust
   host with a bare interpreter (``PYTHONPATH`` at the service root), never syncing the
   service's own dependencies there. The ``[fl_privacy]`` half is the fl-client's, and
   ``check-governance`` validates it with ``flip.nvflare.site_policy --check`` alongside this.

   Exits 0 when valid (or when no document is configured), 1 with the loader's own message
   when not.



Functions
---------

.. autoapisummary::

   check_governance.main


Module Contents
---------------

.. py:function:: main() -> int

