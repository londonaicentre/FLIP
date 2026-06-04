/*
 * Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *     http://www.apache.org/licenses/LICENSE-2.0
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { _http } from "./api";

// Connection-status view of a trust — benign metadata only (no secrets), so
// it's served by the authenticated (non-admin) GET /trust/status endpoint.
// Mirrors the backend ITrustStatus schema.
export interface ITrustStatus {
    id: string;
    name: string;
    code: string | null;
    region: string | null;
    last_heartbeat: string | null;
    project_count: number;
}

// Readable by any authenticated user (Researcher / Viewer / Admin). Errors are
// intentionally NOT swallowed: a rejection lets SWRV keep the page's loader
// rather than rendering a misleading empty federation. Creating a trust stays
// admin-only (see createAdminTrust in admin-trusts-service).
export async function getTrustStatuses(): Promise<ITrustStatus[]> {
    const response = await _http.get<ITrustStatus[]>("/trust/status");

    return Array.isArray(response.data) ? response.data : [];
}
