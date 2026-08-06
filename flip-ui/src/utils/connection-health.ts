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

// Pure derivation of the Connection Status page's health model (issue #901).
// A trust's state is derived from its per-service health snapshot: trust-api
// down → Offline (nothing can be collected); any other service down or
// degraded → Degraded; else Online. The trust-api row itself derives from
// heartbeat age — the snapshot rides on the heartbeat, so a fresh heartbeat is
// the ground truth that trust-api is up. With no snapshot at all (pre-collector
// trust-api builds) every non-core service is "unknown", which never degrades —
// reproducing the page's original heartbeat-only behavior exactly.

import { IServiceHealth, ITrustResponse, ServiceStatus } from "@/services/trust-service";

export type TrustState = "online" | "degraded" | "offline";

// A heartbeat newer than HEARTBEAT_FRESH_S is "online"; older than HEARTBEAT_DEGRADED_S
// (or absent) is "offline"; in between is "degraded". Trust-side poll interval
// defaults to 5s, so 30s/300s gives ~6×/60× the poll cadence.
export const HEARTBEAT_FRESH_S = 30;
export const HEARTBEAT_DEGRADED_S = 5 * 60;

// A snapshot older than this is treated as "no data" — three collector cycles
// (30s each) behind means the collector, or the trust, is not reporting.
export const SERVICES_STALE_S = 90;

export interface IServiceDefinition {
    key: string;
    label: string;
    role: string;
}

// Display registry, in drawer/dots order (design handoff option 1b, with
// data-access-api in the final slot). Keys are the heartbeat wire contract with
// trust-api's health collector; payload keys outside this registry are ignored.
export const SERVICE_REGISTRY: IServiceDefinition[] = [
    {
        key: "trust-api",
        label: "trust-api",
        role: "Core API · heartbeat & auth"
    },
    {
        key: "xnat",
        label: "XNAT",
        role: "Imaging archive"
    },
    {
        key: "imaging-api",
        label: "imaging-api",
        role: "DICOM query / retrieve"
    },
    {
        key: "omop",
        label: "OMOP",
        role: "Cohort database (CDM 5.4)"
    },
    {
        key: "dicom",
        label: "dicom-node",
        role: "PACS / DICOM connector"
    },
    {
        key: "data-access-api",
        label: "data-access-api",
        role: "OMOP cohort queries"
    }
];

export interface IDerivedService extends IServiceDefinition {
    status: ServiceStatus;
    version: string | null;
    response_ms: number | null;
}

export interface IFailingService {
    text: string;
    status: "down" | "degraded";
}

const SERVICE_STATUSES: ServiceStatus[] = ["healthy", "degraded", "down", "unknown"];

// The trust-api service status, derived from heartbeat age (the snapshot rides
// on the heartbeat, so this is the one service the payload cannot vouch for).
export const trustApiStatus = (t: ITrustResponse, nowMs: number = Date.now()): ServiceStatus => {
    if (!t.last_heartbeat) return "down";
    const ageS = (nowMs - new Date(t.last_heartbeat).getTime()) / 1000;
    if (ageS < HEARTBEAT_FRESH_S) return "healthy";
    if (ageS < HEARTBEAT_DEGRADED_S) return "degraded";

    return "down";
};

const snapshotIsFresh = (t: ITrustResponse, nowMs: number): boolean => {
    if (!t.services || !t.services_updated_at) return false;

    return (nowMs - new Date(t.services_updated_at).getTime()) / 1000 <= SERVICES_STALE_S;
};

// Hub-side validation guarantees the vocabulary at write time; guard anyway so
// a newer backend can extend it without breaking older UIs mid-deploy.
const payloadStatus = (entry: IServiceHealth): ServiceStatus =>
    SERVICE_STATUSES.includes(entry.status) ? entry.status : "unknown";

// Registry-ordered per-service rows for the dots column and the drawer.
export const deriveServices = (t: ITrustResponse, nowMs: number = Date.now()): IDerivedService[] => {
    const fresh = snapshotIsFresh(t, nowMs);

    return SERVICE_REGISTRY.map(def => {
        if (def.key === "trust-api") {
            // Status from heartbeat age; version is static info, so the last
            // reported value stays useful even when the snapshot has gone stale.
            return {
                ...def,
                status: trustApiStatus(t, nowMs),
                version: t.services?.["trust-api"]?.version ?? null,
                response_ms: null
            };
        }
        const entry = fresh ? t.services?.[def.key] : undefined;
        if (!entry) {
            return {
                ...def,
                status: "unknown" as ServiceStatus,
                version: null,
                response_ms: null
            };
        }

        return {
            ...def,
            status: payloadStatus(entry),
            version: entry.version ?? null,
            response_ms: entry.response_ms ?? null
        };
    });
};

// The design's pure state derivation: trust-api down → Offline; any service
// down/degraded → Degraded; else Online. "unknown" never degrades.
export const deriveTrustState = (t: ITrustResponse, nowMs: number = Date.now()): TrustState => {
    const services = deriveServices(t, nowMs);
    if (services.find(s => s.key === "trust-api")?.status === "down") return "offline";
    if (services.some(s => s.status === "down" || s.status === "degraded")) return "degraded";

    return "online";
};

// Caption parts for a non-online row ("XNAT down · OMOP degraded"), down first.
export const failingServices = (t: ITrustResponse, nowMs: number = Date.now()): IFailingService[] => {
    const services = deriveServices(t, nowMs);
    if (services.find(s => s.key === "trust-api")?.status === "down") {
        return [
            {
                text: "trust-api down",
                status: "down"
            }
        ];
    }
    const failing = services.filter(s => s.status === "down" || s.status === "degraded");
    failing.sort((a, b) => (a.status === b.status ? 0 : a.status === "down" ? -1 : 1));

    return failing.map(s => ({
        text: `${s.label} ${s.status}`,
        status: s.status as "down" | "degraded"
    }));
};

export const heartbeatText = (iso: string | null, nowMs: number = Date.now()): string => {
    if (!iso) return "never";
    const sec = (nowMs - new Date(iso).getTime()) / 1000;
    if (sec < 60) return `${Math.max(0, Math.floor(sec))}s ago`;
    if (sec < 3_600) return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86_400) return `${Math.floor(sec / 3_600)}h ago`;

    return `${Math.floor(sec / 86_400)}d ago`;
};
