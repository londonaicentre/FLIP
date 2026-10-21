..  Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

.. _release-notes:

#############
Release notes
#############

The notes for each FLIP release are published on
`GitHub Releases <https://github.com/londonaicentre/FLIP/releases>`_. Each release lists what changed and anything
an operator must do when upgrading.

How releases work
=================

- A release is a git tag of the form ``vX.Y.Z``, cut from ``main``. Every container image is rebuilt and published
  with that tag, so a site can run exactly that release.
- The ``flip-utils`` Python package is released to PyPI with the same version number, under a separate
  ``flip-utils-vX.Y.Z`` GitHub release.
- The Central Hub reports the release it runs on its ``/api/health`` endpoint. Trusts should run the same release as
  their hub.

.. tip::

   Use the version selector at the bottom of the sidebar to read these docs as they were for the release you run.

Upgrading
=========

To move a hub or a trust to a new release, follow :doc:`sys-admin/admin-upgrading-sites`.
