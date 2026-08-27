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

// Wire vocabulary for one probed service inside a trust's health snapshot.
// Array and union derive from one literal so the runtime guard in
// connection-health.ts can never drift from the type.
export const SERVICE_STATUSES = ["healthy", "degraded", "down", "unknown"] as const;
export type ServiceStatus = (typeof SERVICE_STATUSES)[number];

// One service entry of the per-trust health snapshot (mirrors the backend
// ServiceHealthEntry): probed status plus optional version and probe latency.
export interface IServiceHealth {
    status: ServiceStatus;
    version: string | null;
    response_ms: number | null;
}

// Trust list item with connection status — benign metadata only (no secrets),
// served by the authenticated (non-admin) GET /trust endpoint. Mirrors the
// backend ITrustStatus schema. The trust pickers only read id/name/code; the
// Connection Status page additionally uses region / last_heartbeat /
// project_count / services. `services` + `services_updated_at` are optional so
// older fixtures and the MirageJS mocks keep compiling: absent means the trust
// never reported a snapshot (pre-collector trust-api build).
export interface ITrustResponse {
    id: string;
    name: string;
    code: string | null;
    region: string | null;
    last_heartbeat: string | null;
    project_count: number;
    services?: Record<string, IServiceHealth> | null;
    services_updated_at?: string | null;
}

// Bootstrap / picker list. Errors are intentionally swallowed → []: a trust-list
// failure is not actionable for the user (the surrounding forms already handle
// "no trusts available" by disabling submit), and throwing would bubble up to a
// generic SWRV error overlay with no recovery path.
export async function getTrusts(): Promise<ITrustResponse[]> {
    try {
        const response = await _http.get<ITrustResponse[]>("/trust");

        return Array.isArray(response.data) ? response.data : [];

    } catch {
        return [];
    }
}

// Connection-status view over the same GET /trust endpoint. Readable by any
// authenticated user (Researcher / Viewer / Admin). Errors are intentionally
// NOT swallowed: a rejection lets SWRV keep the page's loader rather than
// rendering a misleading empty federation. Creating a trust stays admin-only
// (see createAdminTrust in admin-trusts-service).
export async function getTrustStatuses(): Promise<ITrustResponse[]> {
    const response = await _http.get<ITrustResponse[]>("/trust");

    return Array.isArray(response.data) ? response.data : [];
}
