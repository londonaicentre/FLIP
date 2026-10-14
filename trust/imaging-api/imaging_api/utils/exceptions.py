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

# Custom exceptions
class NotFoundError(Exception):
    """Exception raised when a resource is not found."""

    def __init__(self, detail: str = "Resource not found", status_code: int = 404) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")


class AlreadyExistsError(Exception):
    """Exception raised when a resource already exists."""

    pass


class XnatFetchError(Exception):
    """Exception raised when a read from XNAT returns a non-200 response.

    A typed alternative to a bare ``Exception`` for upstream XNAT fetch failures,
    so callers can tell "XNAT itself failed" apart from a local/parsing error. A
    plain ``Exception`` subclass (no custom ``__init__``) so ``str(err)`` keeps
    rendering the raised message verbatim; the routers' generic ``except Exception``
    handler continues to surface it as HTTP 500.
    """

    pass


class XnatZipLayoutError(Exception):
    """Exception raised when an XNAT download zip is not laid out the way FLIP's cache relies on.

    The download cache extracts XNAT's ``structure=default`` zip, whose every member sits under the
    session label (the accession id). A zip with no files, or with a member outside that folder, is
    refused before anything is extracted, so a layout change upstream can never be cached as a
    complete download. A plain ``Exception`` subclass, like ``XnatFetchError``: the download
    router's generic handler surfaces it as HTTP 500.
    """

    pass


class CohortBelowThresholdError(Exception):
    """Exception raised when data-access-api refuses a cohort as below the disclosure threshold.

    A typed alternative to the generic ``RuntimeError`` that ``get_accession_ids`` raises for
    transport failures, so callers can tell a deliberate policy refusal (HTTP 403) apart from
    "the call broke". The distinction matters because the two want opposite handling: a broken
    call should be retried and surfaced as an error, whereas a below-threshold cohort is a
    settled answer — nothing to import, and retrying cannot change it.

    A plain ``Exception`` subclass (no custom ``__init__``) so ``str(err)`` renders the raised
    message verbatim, matching ``XnatFetchError`` above.
    """

    pass


class InternalServerError(Exception):
    """Exception raised for internal server errors."""

    def __init__(self, detail: str = "Internal server error", status_code: int = 500) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")


class LocalStorageError(Exception):
    """Exception raised when writing to the imaging-api local filesystem fails.

    Distinct from NotFoundError so callers can tell "the remote resource
    doesn't exist" apart from "our own disk/mount is misconfigured". The
    former is a user/cohort issue; the latter is an operator/deployment
    issue and should surface as 500, not 404.
    """

    def __init__(self, detail: str = "Local storage error", status_code: int = 500) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")
