# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test bootstrap for the MAP application templates.

Two things have to happen before ``classifier_operator`` is importable, and both belong here rather
than in every test module:

* ``map-apps/`` itself goes on ``sys.path``, so ``classification`` imports as the package it is —
  there is no installable project here, only the templates.
* ``holoscan``'s native extension declares ``libcudart.so.13`` as a link-time dependency but the
  ``holoscan-cu13`` wheel does not carry it — it expects a system CUDA 13. On a CPU-only host (CI)
  the runtime lives in the ``nvidia-cuda-runtime`` wheel instead, under a path the loader never
  searches, so it is preloaded here by soname the same way torch preloads its own nvidia wheels.
  ``LD_LIBRARY_PATH`` cannot do this from inside the process: glibc fixes the search path at exec.
"""

import ctypes
import sys
import sysconfig
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _preload_cuda_runtime() -> None:
    try:
        ctypes.CDLL("libcudart.so.13")
        return  # a system CUDA 13 (or an earlier preload) already provides it
    except OSError:
        pass
    wheel_copy = Path(sysconfig.get_paths()["purelib"]) / "nvidia" / "cu13" / "lib" / "libcudart.so.13"
    if wheel_copy.is_file():
        ctypes.CDLL(str(wheel_copy), mode=ctypes.RTLD_GLOBAL)
    # Neither found: leave the import to fail with holoscan's own ImportError, which names the
    # missing library — clearer than anything raised from here.


_preload_cuda_runtime()
