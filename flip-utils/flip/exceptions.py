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
#

"""Custom exception types for the FLIP package."""


class ResultsUploadError(Exception):
    """Raised when uploading federated training results to S3 fails.

    Distinct from generic training failures so callers can report a model status
    of ``RESULTS_UPLOAD_FAILED`` (training succeeded, results upload did not)
    rather than a blanket ``ERROR``.
    """
