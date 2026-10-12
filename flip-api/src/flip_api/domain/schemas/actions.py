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

from enum import StrEnum


class ProjectAuditAction(StrEnum):
    DELETE = "DELETE"
    EDIT = "EDIT"
    APPROVE = "APPROVE"
    STAGE = "STAGE"
    UNSTAGE = "UNSTAGE"
    # Per-trust decisions on a staged project; the audit row's trust_id names the trust.
    APPROVE_TRUST = "APPROVE_TRUST"
    DECLINE_TRUST = "DECLINE_TRUST"


class ModelAuditAction(StrEnum):
    DELETE = "DELETE"
    EDIT = "EDIT"
    # Status-transition audits keyed by the new ModelStatus value. Recorded in
    # update_model_status so the model lifecycle UI can render real dates for
    # "Prepared / Running / Results uploaded" instead of static "done"
    # labels. Only stages surfaced in the lifecycle bar are tracked.
    PREPARED = "PREPARED"
    RUNNING = "RUNNING"
    RESULTS_UPLOADED = "RESULTS_UPLOADED"


class TrustAuditAction(StrEnum):
    """Lifecycle events for the trust registry.

    Captured by `trusts_services.utils.audit_helper.audit_trust_action`:
    `register_trust` writes REGISTERED, `delete_trust` writes DELETED. The
    audit row is stored in `trusts_audit` with no FK to `trust.id`, so it
    persists past a hard delete.

    ADMIN_ADDED / ADMIN_REMOVED record a user becoming, or ceasing to be, the trust's
    Trust Admin (FLIP#1258); the row's ``subject_user_id`` names that user. They live in
    this registry rather than the user audit because the question an operator asks later —
    "who could decide for this trust, and since when" — is a question about the trust.
    """

    REGISTERED = "REGISTERED"
    DELETED = "DELETED"
    ADMIN_ADDED = "ADMIN_ADDED"
    ADMIN_REMOVED = "ADMIN_REMOVED"
