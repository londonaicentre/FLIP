# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

from enum import IntEnum


class ProjectPreArchiveSettings(IntEnum):
    """
    Settings for how XNAT should handle pre-archive behavior.
    See https://wiki.xnat.org/xnat-api/prearchive-api#PrearchiveAPI-SetProjectPrearchiveSettings
    """

    # All uploaded image data will be placed into the prearchive. Users will have to manually transfer
    # sessions into the permanent archive
    SEND_ALL_TO_PRE_ARCHIVE = 0
    # All uploaded image data will be auto-archived. If a session with the same label already exists,
    # new files will NOT overwrite old ones
    SEND_ALL_TO_ARCHIVE_AND_IGNORE_EXISTING = 4
    # All uploaded image data will be auto-archived. If a session with the same label already exists,
    # new files WILL overwrite old ones
    SEND_ALL_TO_ARCHIVE_AND_OVERWRITE_EXISTING = 5
