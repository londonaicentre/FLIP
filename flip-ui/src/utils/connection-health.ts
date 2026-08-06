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

import { IServiceHealth, ITrustResponse, SERVICE_STATUSES, ServiceStatus } from "@/services/trust-service";
import { apiTimestampMs, relativeAgeLabel } from "@/utils/helpers";

export type TrustState = "online" | "degraded" | "offline";

// Thresholds for the trust-api row's own status — the service vocabulary, not the
// trust state: newer than HEARTBEAT_FRESH_S is "healthy"; older than
// HEARTBEAT_DEGRADED_S (or absent) is "down", which stateFromServices turns into
// Offline; in between is "degraded". The trust-side poll interval defaults to 5s,
// so 30s/300s gives ~6×/60× the poll cadence.
export const HEARTBEAT_FRESH_S = 30;
export const HEARTBEAT_DEGRADED_S = 5 * 60;

// A snapshot at most this old (inclusive) counts as live data. The hub re-stamps
// services_updated_at on every snapshot-carrying heartbeat (~5s), so the stamp
// freezes only when the trust stops sending snapshots — the collector dropped its
// snapshot (failed, hung or dead), or heartbeats stopped — about 18 missed
// heartbeats of tolerance. A trust that never reported doesn't reach this path
// (the !t.services guard catches it first). Caveat: the poller runs tasks between
// heartbeats, so a long cohort query can delay them past this window on an
// otherwise healthy trust — see FLIP#920.
export const SERVICES_STALE_S = 90;

export interface IServiceDefinition {
    key: string;
    label: string;
    role: string;
}

// Display registry, in drawer/dots order (design handoff option 1b, with
// data-access-api in the final slot). Keys are the heartbeat wire contract with
// trust-api's health collector; payload keys outside this registry are ignored.
export const SERVICE_REGISTRY = [
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
        role: "Cohort database (OMOP CDM)"
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
] as const satisfies readonly IServiceDefinition[];

// Registry-key union — typing consumer maps (e.g. the drawer's icon map) with it
// turns "added a service but forgot its icon" into a compile error.
export type ServiceKey = (typeof SERVICE_REGISTRY)[number]["key"];

export interface IDerivedService extends Omit<IServiceDefinition, "key"> {
    // Narrowed to the registry union: derived rows only ever come from
    // SERVICE_REGISTRY, so consumers can index icon/label maps without casts.
    key: ServiceKey;
    status: ServiceStatus;
    version: string | null;
    response_ms: number | null;
}

// The statuses that count as "failing", derived from the vocabulary rather than
// restated — so a future status can't be added to one definition and not another.
export type FailingStatus = Exclude<ServiceStatus, "healthy" | "unknown">;

export interface IFailingService {
    text: string;
    status: FailingStatus;
}

// A trust row with its health derived once. The page builds these per data refresh
// and passes them on (drawer, tiles, sort), so every surface reads the same
// derivation at the same instant — deriving again elsewhere would use a second
// Date.now() and could straddle a threshold, showing Online in the row and
// Degraded in the drawer at the same moment.
export interface IDerivedTrust extends ITrustResponse {
    _state: TrustState;
    _services: IDerivedService[];
    _failing: IFailingService[];
}

// Trust-state display vocabulary, shared by the page's status pills and the
// drawer header (single home so the two can't drift — role-appearance.ts idiom).
export const STATE_LABELS: Record<TrustState, string> = {
    online: "Online",
    degraded: "Degraded",
    offline: "Offline"
};
export const PILL_CLASSES: Record<TrustState, string> = {
    online: "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-100",
    degraded: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
    offline: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200"
};

// The trust-api service status, derived from heartbeat age (the snapshot rides
// on the heartbeat, so this is the one service the payload cannot vouch for).
export const trustApiStatus = (t: ITrustResponse, nowMs: number = Date.now()): ServiceStatus => {
    const beatMs = apiTimestampMs(t.last_heartbeat);
    if (beatMs === null) return "down";
    const ageS = (nowMs - beatMs) / 1000;
    if (ageS < HEARTBEAT_FRESH_S) return "healthy";
    if (ageS < HEARTBEAT_DEGRADED_S) return "degraded";

    return "down";
};

const snapshotIsFresh = (t: ITrustResponse, nowMs: number): boolean => {
    if (!t.services) return false;
    const stampMs = apiTimestampMs(t.services_updated_at);
    if (stampMs === null) return false;

    return (nowMs - stampMs) / 1000 <= SERVICES_STALE_S;
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

        return {
            ...def,
            status: entry ? payloadStatus(entry) : "unknown",
            version: entry?.version ?? null,
            response_ms: entry?.response_ms ?? null
        };
    });
};

// The design's pure state derivation over an already-derived row set: trust-api
// down → Offline; any service down/degraded → Degraded; else Online. "unknown"
// never degrades.
export const stateFromServices = (services: IDerivedService[]): TrustState => {
    if (services.find(s => s.key === "trust-api")?.status === "down") return "offline";
    if (services.some(s => s.status === "down" || s.status === "degraded")) return "degraded";

    return "online";
};

export const deriveTrustState = (t: ITrustResponse, nowMs: number = Date.now()): TrustState =>
    stateFromServices(deriveServices(t, nowMs));

export const isFailing = (s: IDerivedService): s is IDerivedService & { status: FailingStatus } =>
    s.status === "down" || s.status === "degraded";

// Caption parts for a non-online row ("XNAT down · OMOP degraded"), down first —
// except when trust-api is down, where the caption collapses to just that, since
// nothing else can be known about a trust that isn't reporting.
export const failingFromServices = (services: IDerivedService[]): IFailingService[] => {
    if (services.find(s => s.key === "trust-api")?.status === "down") {
        return [
            {
                text: "trust-api down",
                status: "down"
            }
        ];
    }
    const failing = services.filter(isFailing);
    failing.sort((a, b) => (a.status === b.status ? 0 : a.status === "down" ? -1 : 1));

    return failing.map(s => ({
        text: `${s.label} ${s.status}`,
        status: s.status
    }));
};

// Derive every health-related field for one trust, in one pass at one instant.
export const deriveTrust = (t: ITrustResponse, nowMs: number = Date.now()): IDerivedTrust => {
    const services = deriveServices(t, nowMs);

    return {
        ...t,
        _state: stateFromServices(services),
        _services: services,
        _failing: failingFromServices(services)
    };
};

// "6s ago"-style heartbeat age; "never" when absent or unparseable.
export const heartbeatText = (iso: string | null, nowMs: number = Date.now()): string => {
    const beatMs = apiTimestampMs(iso);
    if (beatMs === null) return "never";

    return relativeAgeLabel((nowMs - beatMs) / 1000);
};
