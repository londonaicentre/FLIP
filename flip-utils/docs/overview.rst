Overview
========

``flip-utils`` is the pip-installable distribution published from this
repository. Its Python import package is ``flip``, which contains the shared
platform logic used by FLIP jobs and services.

Package layout
--------------

``flip.core``
   Base classes and the ``FLIP()`` factory.

``flip.constants``
   Environment-backed settings, enums, and shared constants.

``flip.utils``
   Utility helpers and model-weight handling helpers.

``flip.nvflare``
   NVFLARE-specific metrics, executors, controllers, and components.

Build the docs locally
----------------------

From the repository root, run:

.. code-block:: bash

   make docs

The generated HTML site will be written to ``docs/_build/html``.

How the API reference is generated
----------------------------------

The API reference is built with ``sphinx-autoapi`` and points directly at the
``flip/`` source tree. That keeps the reference pages aligned with the code
without maintaining hand-written module stubs.
