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

.. _user-models:

####################
Models and files
####################

.. admonition:: Before you start
   :class: note

   The project must be approved before you can create models and start training (see :ref:`project-approval`).

A project can hold several **models**. Each model is one FL app: the code and configuration you upload, which FLIP
then trains across the participating Trusts.

.. _create-model:

**************
Create a model
**************

1. Open the project page.
2. Click **Create Model**.
3. Enter a name and a short description.
4. Click **Create Model**.

.. figure:: ../assets/generated/gifs/flip/create-model.gif
   :width: 600
   :align: center

   Creating a model.

************
Edit a model
************

You can edit a model until its training starts.

1. Open the model from the project page.
2. Click **Edit Model**.
3. Update the model's name or description.
4. Click **Update Model**.

.. figure:: ../assets/generated/gifs/flip/edit-model.gif
   :width: 600
   :align: center

   Editing a model.

**************
Delete a model
**************

Only the project owner, the user who created the model, or a user with the ``admin`` role can delete a model. You can
delete it at any time.

.. warning::

   Deleting a model during training stops the training, and the model can no longer be reached.

1. Open the model from the project page.
2. Click **Edit Model**.
3. Under **Danger Zone**, click **Delete Model**.
4. Enter the model name.
5. Click **Continue**.

.. figure:: ../assets/generated/gifs/flip/delete-model.gif
   :width: 600
   :align: center

   Deleting a model.

.. _model-files:

***********
Model files
***********

A model's files are your FL app: the Python code, its ``config.json`` and anything else it needs at run time. Create the
model first, then upload its files.

Which files are required
========================

There is no single list. The required files depend on the model's **job type**, the kind of federated job it runs (such
as federated averaging or evaluation), and on the platform's FL backend.

- The app declares its job type with the ``job_type`` key in ``config.json``. Without the key, the job type is
  ``standard``.
- ``config.json`` itself is required for every job type on both backends. A model without one cannot start training.

**You do not need to look this up.** The model page tells you:

- The **Model Files** panel shows the job type detected from your uploaded ``config.json``.
- The **Model Files** panel lists the files that job type requires and marks any still missing, and the training
  options on the right show a **Required files** summary.

Both update as soon as you upload or replace ``config.json``. Training cannot start until every required file is
uploaded.

You can upload more files than the required set. Anything your code imports, such as helper modules or transforms, is
bundled with the app and sent to the participating Trusts, with two exceptions:

- A file with the same name as one the platform's app template supplies is dropped in favour of the template's copy.
- A checkpoint declared for server-side use stays on the FL server and is not sent to the Trusts.

.. seealso::

   :ref:`fl-required-files` and the :ref:`FL nets component page <flip-fl-nets>` for the full file list per job type,
   and the :gh:`FLIP tutorials <fl-tutorials>` for complete, working
   apps.

.. important::

   **Your app must not download anything while it runs.** Every weight, checkpoint or auxiliary network it needs must be
   one of the files you upload. Calls such as ``pretrained=True``, ``torch.hub.load(...)``,
   ``from_pretrained("org/model")`` or ``load_state_dict_from_url(...)`` fetch from the internet when the model is
   built, and that fails in two ways:

   - **It does not work where the app runs.** The FL server on a platform-managed estate and a Trust's training host
     behind an NHS firewall have no internet route. The download hangs and the run dies: on FLARE the Trust reports
     ``cannot sync with server Runner``; on Flower the run never starts a round.
   - **It bypasses the checks.** Uploaded files are checked once and then sent exactly as uploaded, so a Trust can
     inspect what will run. A URL fetched later can serve different bytes, and loading a swapped ``.pt`` checkpoint runs
     whatever it contains, on the FL server and next to a Trust's data.

   Upload the weights instead, as ``.safetensors`` where you can (tensors only, nothing to execute) or as
   ``.pt``/``.pth``, which the platform scans (see :ref:`upload-model-files`). Build the model with
   ``pretrained=False`` and load the file from beside your code. The
   :gh:`latent-diffusion tutorial <fl-tutorials/nvflare/image_synthesis/latent_diffusion_model>`
   shows how to ship a network a library would otherwise fetch through ``torch.hub``.

.. tip::

   Test your files locally with the FLIP tutorials workflow before uploading, and check them for syntax errors. Linters
   such as `Pylint <https://pypi.org/project/pylint/>`_ and `JSON Lint <https://www.npmjs.com/package/jsonlint>`_ are a
   quick way to check Python and JSON.

.. _upload-model-files:

Upload files
============

1. Open the model from the project page.
2. Go to the **Model Files** section on the left of the model page.
3. Browse to your files, or drag and drop them into the box.
4. Wait for the confirmation that the upload finished.
5. Wait for each file's check. A magnifying-glass icon means the file is being checked; it turns into a document icon
   when the file passes and is ready to use.

.. figure:: ../assets/generated/gifs/flip/upload-file.gif
   :width: 600
   :align: center

   Uploading files.

Checking usually takes seconds; a large checkpoint takes longer. You can leave the page meanwhile: the check runs on the
server and the status is up to date when you return. **Training cannot start until every file has passed.**

**How files are checked.** Every upload waits in a staging area until it has been checked. Until then it cannot be used
for training and is never sent to a Trust.

- Files that carry Python pickle data (``.pt``, ``.pth``, ``.pkl``), including model checkpoints, are
  **scanned for unsafe content**, because loading one runs whatever it contains. A file that fails is marked with a
  red virus icon and deleted from storage. Delete the entry and upload a corrected file.
- Python files are checked for type, then released and run as they are at every participating Trust (see the warning
  below).

.. warning::

   **FLIP does not block Python code because of what it does.** An advisory scan flags common risky patterns, such as
   ``subprocess``, ``os.system``, ``eval`` or hard-coded credentials, with an amber indicator on the model's file list.
   You and anyone who opens the model page later can see it.

   The scan is a signal, not a gate. It does not stop obfuscated or undetected code from running. It also flags some
   routine ML patterns, such as ``torch.load`` or ``assert``, so an indicator does not by itself mean a file is unsafe.
   Only upload code you wrote or reviewed yourself, and treat third-party code as untrusted.

Upload limits:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Limit
     - Default
   * - File types
     - ``.py``, ``.json``, ``.toml``, ``.pt``, ``.pth``, ``.pkl``, ``.txt``, ``.yaml``, ``.yml`` and
       ``.safetensors``. Anything else, including archives such as ``.zip``, is refused with a message listing the
       accepted types.
   * - File size
     - **5 GiB** per file (the deployment's ``MAX_MODEL_FILE_BYTES``). The page checks the size before the upload
       starts, and storage rejects an oversized file.
   * - Upload time
     - **30 minutes**. The upload authorisation expires after that, so a single large upload must finish within it.

.. _training-configuration:

Training configuration
======================

Besides your code, an app carries a small configuration file. It sets how the federated run behaves: how many rounds
it trains for, how client updates are combined, and any settings your own code reads. Where it lives depends on the FL
backend:

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Backend
     - Configuration
   * - FLARE
     - ``config.json``, required for every FLARE job type. Besides ``job_type``, it can set platform keys such as
       ``GLOBAL_ROUNDS`` and ``LOCAL_ROUNDS``. Keys the platform does not recognise are passed through unchanged for
       your code to read, which is how the tutorials set things like learning rate or validation split.
   * - Flower
     - ``config.json`` is read only for ``job_type``. The run configuration comes from the platform's app template,
       and your app can override it with a ``config.toml`` file. ``config.toml`` may only override keys the template
       already declares: an undeclared key fails the run at submission. Flower apps do not use the FLARE keys.

Every platform key has a default, so an app that sets only ``job_type`` still runs.

.. seealso::

   :ref:`fl-training-configuration` on the FL nets component page for every key, its accepted values and its default
   on each backend.

View and delete files
=====================

1. Open the model from the project page.
2. Go to the **Model Files** section on the left of the model page.
3. Click the download icon to view a file's contents, or the bin icon to delete it.

You can then upload a replacement.

.. figure:: ../assets/generated/gifs/flip/manage-files.gif
   :width: 600
   :align: center

   Managing uploaded files.
