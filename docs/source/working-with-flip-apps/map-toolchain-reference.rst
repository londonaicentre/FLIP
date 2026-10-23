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

.. _map-toolchain-reference:

#######################
MAP toolchain reference
#######################

The version pins, error messages and GPU measurements behind
:doc:`wrap-model-for-deployment`. Follow that guide to package a model; come here when an install or a ``holoscan``
command fails, or when a deployment site's GPU driver does not meet the requirements.

.. contents:: On this page
   :local:
   :depth: 2


.. _map-toolchain-pins:

Pinned versions
===============

Pin these versions. The MONAI Deploy tooling has changed a lot between releases, and unpinned instructions break
quickly. The MAP templates' ``requirements.txt`` files carry the same pins.

.. list-table::
   :header-rows: 1
   :widths: 32 22 46

   * - Component
     - Version
     - Notes
   * - ``monai-deploy-app-sdk``
     - ``4.0.0``
     - The CUDA-13 generation. Requires ``holoscan-cu13``.
   * - ``holoscan-cu13`` / ``holoscan-cli``
     - ``4.2.0``
     - Install **both**: the packager refuses to run with only the CLI. The templates pin only ``holoscan-cu13``, so
       pin the CLI yourself — ``holoscan-cli`` **4.3.0 removed MAP packaging**, and the App SDK does not pin it, so an
       unconstrained install gives you a CLI without ``holoscan package --models``.
   * - ``monai``
     - ``>=1.6.0``
     - What FLIP trains with; 1.6.0 also patches four high-severity advisories. **Not** ``1.5.0``, which caps torch
       below 2.7 and so cannot use Blackwell GPUs.
   * - ``torch``
     - ``2.13.0``
     - PyPI's default wheel (CUDA 13.0).
   * - **NVIDIA driver**
     - **>= 580**
     - Required on the packaging host **and** on every host that runs the MAP. Check with ``nvidia-smi`` before you
       start.
   * - Python
     - ``3.12``
     - The MAP base image must be Ubuntu 24.04 to match.

You also need Docker with the NVIDIA Container Toolkit.

.. _map-previous-generation:

Previous toolchain generation (driver below 580)
------------------------------------------------

For a deployment site whose NVIDIA driver is below 580, use the previous generation **as a complete set**. Do not
loosen the current pins one at a time, and do not mix the two sets.

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Component
     - Version
   * - ``monai``
     - ``>=1.6.0``
   * - ``torch``
     - ``2.11.0+cu128``
   * - ``holoscan-cu12``
     - ``3.11.0``
   * - ``monai-deploy-app-sdk``
     - ``3.5.0`` (requires Holoscan 3.x; 4.0.0 requires ``holoscan-cu13``)
   * - ``holoscan-cli``
     - ``3.11.0``, with a local ``--source`` manifest
   * - ``holoscan package``
     - ``--cuda 12``

This set is one commit back in the guide's history and produces the same DICOM SEG.


.. _map-environment-traps:

Environment traps
=================

All of these came up while establishing the packaging steps, and none is documented upstream.

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - Symptom
     - Cause and remedy
   * - ``nvidia-container-cli: unsatisfied condition: cuda>=13.0``
     - The host driver is below 580 and cannot run the CUDA-13 MAP. Upgrade it, or build the
       :ref:`previous generation <map-previous-generation>`.
   * - ``monai-deploy: command not found``
     - App SDK 4.x has no command-line tool; packaging moved to the ``holoscan`` CLI. The 4.x documentation has not
       caught up.
   * - ``holoscan package`` has no ``--models`` flag, or ``holoscan package: error: unrecognized arguments:
       --models`` (or a ``package`` subcommand that talks about DEB/WHEEL generators)
     - ``holoscan-cli`` 4.3.0 or later, which removed MAP packaging. An unconstrained install gives you this, because
       the App SDK does not pin the CLI. Pin ``holoscan-cli==4.2.0`` (or 3.x for the previous generation).
   * - ``# of series: 0``, then ``Missing expected input 'study_selected_series_list'``
     - The loader dropped every instance. If ``Error parsing SOP Instance: int() argument must be ... not
       'NoneType'`` appears just above, a numeric tag is **present but empty**. **FLIP's mock CT data before version
       20260821 has this**: every spleen series seeded from ``aicentreflip/trust-data`` carries an empty
       ``SeriesNumber`` and ``AcquisitionNumber``. (The mock radiograph data omits these tags altogether, so it is
       not affected.) Version 20260821 fills both; on older data, stamp them before packaging::

           import glob

           import pydicom

           for path in glob.glob("input/*.dcm"):
               ds = pydicom.dcmread(path)
               ds.AcquisitionNumber = 1
               ds.SeriesNumber = 1
               ds.save_as(path, enforce_file_format=True)

   * - ``No installed Holoscan PyPI package found``
     - The packaging host needs the Holoscan **SDK**, not only the CLI. Install ``holoscan-cu13==4.2.0`` in the same
       environment; ``holoscan version`` should then stop reporting ``Holoscan SDK: N/A``.
   * - ``ManifestDownloadError ... 404``
     - The CLI downloads the base-image manifest from GitHub, where only version 4.2.0's is published — one reason
       for the pin. On any other CLI version, write the manifest yourself and pass it with ``--source``.
   * - ``E: Version '3.12.3-*' for 'python3' was not found``
     - The CLI pins Ubuntu 24.04 package versions, so the base image in the manifest must be Ubuntu 24.04.
   * - ``ModuleNotFoundError`` inside the MAP
     - The build and run users differ. Pass ``--uid`` / ``--gid`` when packaging.
   * - ``undefined symbol: cudaGetDriverEntryPointByVersion``
     - Mixed CUDA generations — a CUDA-12 torch under a CUDA-13 base image, or the reverse. Keep ``torch``,
       ``holoscan-cu13`` and ``--cuda`` in agreement.
   * - ``no kernel image is available for execution on the device``
     - torch below 2.7 cannot use a Blackwell GPU, yet ``torch.cuda.is_available()`` still returns ``True``, so it
       fails late. Caused by ``monai<=1.5.0``, which caps torch below 2.7; use ``monai>=1.6.0``.
   * - ``The NVIDIA driver on your system is too old``
     - The driver is below 580. Upgrade it, or build the :ref:`previous generation <map-previous-generation>`.
   * - ``ItemNotExistsError: A predictor of the model is not set``
     - A **directory** bundle packaged with the segmentation template. Use the TorchScript form; see
       :ref:`map-directory-bundle-limitation`.


.. _map-maintainers:

For maintainers
===============

Background behind the version pins and the directory-bundle limitation. App authors do not need it to package a
model.

.. _map-gpu-compatibility:

Why these versions: GPU compatibility measurements
--------------------------------------------------

The templates originally carried ``monai<=1.5.0`` on the CUDA-12 toolchain, copied from the App SDK's
``ai_spleen_seg_app`` example. Nothing required it — the App SDK does not depend on MONAI directly — and 1.5.0 is
the only nearby release that still caps ``torch<2.7.0``. Measured on an RTX 5090:

.. list-table::
   :header-rows: 1
   :widths: 34 22 14 14 16

   * - requirements
     - resolves to
     - ``is_available()``
     - a real kernel
     - the MAP's DICOM SEG
   * - ``monai<=1.5.0`` + ``torch``
     - torch 2.6.0+cu124
     - ``True``
     - **fails**, no ``sm_120``
     - never produced
   * - ``monai>=1.5.2`` + ``torch``, driver 575
     - torch 2.13.0+cu130
     - ``False``
     - fails, driver < 580
     - never produced
   * - ``monai>=1.5.2`` + ``torch==2.11.0+cu128``
     - torch 2.11.0+cu128
     - ``True``
     - works
     - 73 frames, 212,808 voxels
   * - ``monai>=1.6.0`` + ``torch==2.11.0+cu128``
     - torch 2.11.0+cu128
     - ``True``
     - works
     - **identical, voxel for voxel**
   * - **the current pins**, driver 580
     - torch 2.13.0+cu130
     - ``True``
     - works
     - **identical, voxel for voxel**

- **MONAI 1.6.0 changes nothing about what the MAP computes**: same 73 frames, 212,808 foreground voxels and 278 mL
  against the published tutorial checkpoint and a 168-slice spleen CT. ``UNet`` and ``DenseNet121``, the two
  tutorial architectures, have identical state-dict keys and shapes in 1.5.2 and 1.6.0, so the directory form's
  MONAI coupling does not affect them — a fact about those two releases, not a guarantee for the next.
- **The CUDA-13 move costs a driver floor and buys a torch line that still moves.** PyTorch publishes no CUDA-12.8
  wheel above 2.11.0, so the previous pins were a permanent ceiling: a torch advisory fixed in 2.12 or later would
  have forced this migration under time pressure.

.. _map-directory-bundle-limitation:

Why a directory bundle fails in the segmentation template
---------------------------------------------------------

The packager copies the bundle into ``/opt/holoscan/models``. The App SDK's ``ModelFactory`` does not recognise that
layout — ``NamedModel`` expects exactly one item per model folder, and a bundle has three — yet still returns a
placeholder ``Model`` with ``predictor = None``, so ``app_context.models`` is truthy. ``MonaiBundleInferenceOperator``
checks ``app_context.models`` *before* its own directory-bundle branch, takes the placeholder, and fails much later
inside the inferer. ``core/models/named_model.py`` and ``core/models/factory.py`` are byte-identical in App SDK 3.5.0
and 4.0.0.

The classification template is unaffected because its operator loads the bundle itself and checks that the model has
a predictor. If ``torch.jit`` is ever removed, the SDK's own ``torch.jit.load`` breaks at the same time, so this has
to be fixed upstream regardless.

Other notes
-----------

- TorchScript is deprecated from torch 2.5 and reports itself unsupported on Python 3.14+. The App SDK declares
  ``requires-python = ">=3.10,<3.14"``.
- The empty ``SeriesNumber`` in the old mock CT data fails first, in ``populate_series_attributes``; the empty
  ``AcquisitionNumber`` is reached only where two instances share a position.

.. seealso::

   :gh:`map-apps/README.md` and :gh:`flip-utils/flip/export/README.md` carry the template and exporter detail.
