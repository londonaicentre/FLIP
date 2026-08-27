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

import { describe, expect, it } from "vitest";

import { IServiceHealth, ITrustResponse } from "@/services/trust-service";
import { deriveServices,
    deriveTrustState,
    failingFromServices,
    heartbeatText,
    SERVICE_REGISTRY,
    SERVICES_STALE_S,
    trustApiStatus } from "@/utils/connection-health";

const NOW = Date.parse("2026-08-06T12:00:00.000Z");

const secondsAgo = (s: number): string => new Date(NOW - s * 1000).toISOString();

const makeTrust = (overrides: Partial<ITrustResponse> = {}): ITrustResponse => ({
    id: "trust-1",
    name: "Guy's and St Thomas'",
    code: "GSTT",
    region: "London",
    last_heartbeat: secondsAgo(5),
    project_count: 3,
    services: null,
    services_updated_at: null,
    ...overrides
});

const healthyServices = (): Record<string, IServiceHealth> => ({
    "trust-api": {
        status: "healthy",
        version: "0.3.0",
        response_ms: null
    },
    "imaging-api": {
        status: "healthy",
        version: "0.3.0",
        response_ms: 12
    },
    "data-access-api": {
        status: "healthy",
        version: "0.3.0",
        response_ms: 15
    },
    xnat: {
        status: "healthy",
        version: "1.10.0",
        response_ms: 220
    },
    dicom: {
        status: "healthy",
        version: null,
        response_ms: 31
    },
    omop: {
        status: "healthy",
        version: null,
        response_ms: 2
    }
});

describe("trustApiStatus", () => {
    it("is healthy under the fresh threshold and degraded from 30s", () => {
        expect(trustApiStatus(makeTrust({ last_heartbeat: secondsAgo(29) }), NOW)).toBe("healthy");
        expect(trustApiStatus(makeTrust({ last_heartbeat: secondsAgo(31) }), NOW)).toBe("degraded");
    });

    it("is degraded under 300s and down from 300s", () => {
        expect(trustApiStatus(makeTrust({ last_heartbeat: secondsAgo(299) }), NOW)).toBe("degraded");
        expect(trustApiStatus(makeTrust({ last_heartbeat: secondsAgo(301) }), NOW)).toBe("down");
    });

    it("is down when no heartbeat was ever recorded", () => {
        expect(trustApiStatus(makeTrust({ last_heartbeat: null }), NOW)).toBe("down");
    });
});

describe("SERVICE_REGISTRY", () => {
    it("lists the four FLIP-supplied components first, then the two the institution owns", () => {
        expect(SERVICE_REGISTRY.map(s => s.key)).toEqual([
            "trust-api",
            "data-access-api",
            "imaging-api",
            "xnat",
            "omop",
            "dicom"
        ]);
    });
});

describe("deriveServices", () => {
    it("returns entries in registry order with payload statuses when fresh", () => {
        const t = makeTrust({
            services: healthyServices(),
            services_updated_at: secondsAgo(10)
        });

        const derived = deriveServices(t, NOW);

        expect(derived.map(s => s.key)).toEqual(SERVICE_REGISTRY.map(s => s.key));
        expect(derived.find(s => s.key === "xnat")).toMatchObject({
            status: "healthy",
            version: "1.10.0",
            response_ms: 220
        });
    });

    it("forces non-core services to unknown when the snapshot is stale", () => {
        const t = makeTrust({
            services: healthyServices(),
            services_updated_at: secondsAgo(SERVICES_STALE_S + 1)
        });

        const xnat = deriveServices(t, NOW).find(s => s.key === "xnat");

        expect(xnat).toMatchObject({
            status: "unknown",
            version: null,
            response_ms: null
        });
    });

    it("keeps payload statuses at the staleness fence", () => {
        const t = makeTrust({
            services: healthyServices(),
            services_updated_at: secondsAgo(SERVICES_STALE_S - 1)
        });

        expect(deriveServices(t, NOW).find(s => s.key === "xnat")?.status).toBe("healthy");
    });

    it("reports unknown for every non-core service when no snapshot was ever sent", () => {
        const derived = deriveServices(makeTrust(), NOW);

        for (const entry of derived.filter(s => s.key !== "trust-api")) {
            expect(entry.status).toBe("unknown");
        }
    });

    it("reports unknown for a service key absent from the payload", () => {
        const services = healthyServices();
        delete (services as Record<string, unknown>).omop;
        const t = makeTrust({
            services,
            services_updated_at: secondsAgo(5)
        });

        expect(deriveServices(t, NOW).find(s => s.key === "omop")?.status).toBe("unknown");
    });

    it("maps an out-of-vocabulary payload status to unknown", () => {
        const services = healthyServices();
        (services.xnat as { status: string }).status = "sideways";
        const t = makeTrust({
            services,
            services_updated_at: secondsAgo(5)
        });

        expect(deriveServices(t, NOW).find(s => s.key === "xnat")?.status).toBe("unknown");
    });


    it("ignores payload keys outside the registry", () => {
        const services = healthyServices();
        services["mystery-svc"] = {
            status: "down",
            version: null,
            response_ms: null
        };
        const t = makeTrust({
            services,
            services_updated_at: secondsAgo(5)
        });

        const derived = deriveServices(t, NOW);

        expect(derived).toHaveLength(SERVICE_REGISTRY.length);
        expect(derived.some(s => s.key === "mystery-svc")).toBe(false);
    });

    it("treats a snapshot without a timestamp as no data", () => {
        const t = makeTrust({
            services: healthyServices(),
            services_updated_at: null
        });

        expect(deriveServices(t, NOW).find(s => s.key === "xnat")?.status).toBe("unknown");
    });

    it("tolerates a payload missing the trust-api entry", () => {
        const services = healthyServices();
        delete (services as Record<string, unknown>)["trust-api"];
        const t = makeTrust({
            services,
            services_updated_at: secondsAgo(5)
        });

        const api = deriveServices(t, NOW).find(s => s.key === "trust-api");

        expect(api?.status).toBe("healthy");
        expect(api?.version).toBeNull();
    });

    it("derives the trust-api entry from heartbeat age, not the payload", () => {
        const t = makeTrust({
            last_heartbeat: secondsAgo(301),
            services: healthyServices(),
            services_updated_at: secondsAgo(5)
        });

        const api = deriveServices(t, NOW).find(s => s.key === "trust-api");

        expect(api?.status).toBe("down");
    });

    it("keeps the trust-api version from the last snapshot even when stale", () => {
        const t = makeTrust({
            last_heartbeat: secondsAgo(600),
            services: healthyServices(),
            services_updated_at: secondsAgo(600)
        });

        const api = deriveServices(t, NOW).find(s => s.key === "trust-api");

        expect(api?.version).toBe("0.3.0");
        expect(api?.response_ms).toBeNull();
    });
});

describe("deriveTrustState", () => {
    it("is offline when trust-api is down, regardless of the payload", () => {
        const t = makeTrust({
            last_heartbeat: secondsAgo(301),
            services: healthyServices(),
            services_updated_at: secondsAgo(5)
        });

        expect(deriveTrustState(t, NOW)).toBe("offline");
    });

    it("is degraded when any service is down while trust-api is alive", () => {
        const services = healthyServices();
        services.xnat.status = "down";
        const t = makeTrust({
            services,
            services_updated_at: secondsAgo(5)
        });

        expect(deriveTrustState(t, NOW)).toBe("degraded");
    });

    it("is degraded when a service is degraded", () => {
        const services = healthyServices();
        services.omop.status = "degraded";
        const t = makeTrust({
            services,
            services_updated_at: secondsAgo(5)
        });

        expect(deriveTrustState(t, NOW)).toBe("degraded");
    });

    it("is degraded when the heartbeat itself is aging (30–300s)", () => {
        const t = makeTrust({
            last_heartbeat: secondsAgo(60),
            services: healthyServices(),
            services_updated_at: secondsAgo(5)
        });

        expect(deriveTrustState(t, NOW)).toBe("degraded");
    });

    it("is online when every reported service is healthy", () => {
        const t = makeTrust({
            services: healthyServices(),
            services_updated_at: secondsAgo(5)
        });

        expect(deriveTrustState(t, NOW)).toBe("online");
    });

    it("never degrades on unknown — no payload reproduces heartbeat-only behavior", () => {
        expect(deriveTrustState(makeTrust({ last_heartbeat: secondsAgo(5) }), NOW)).toBe("online");
        expect(deriveTrustState(makeTrust({ last_heartbeat: secondsAgo(60) }), NOW)).toBe("degraded");
        expect(deriveTrustState(makeTrust({ last_heartbeat: secondsAgo(301) }), NOW)).toBe("offline");
        expect(deriveTrustState(makeTrust({ last_heartbeat: null }), NOW)).toBe("offline");
    });

    it("tolerates trusts predating the services fields entirely", () => {
        const t = makeTrust();
        delete (t as Partial<ITrustResponse>).services;
        delete (t as Partial<ITrustResponse>).services_updated_at;

        expect(deriveTrustState(t, NOW)).toBe("online");
    });
});

describe("failingFromServices", () => {
    it("is empty for an online trust", () => {
        const t = makeTrust({
            services: healthyServices(),
            services_updated_at: secondsAgo(5)
        });

        expect(failingFromServices(deriveServices(t, NOW))).toEqual([]);
    });

    it("reports only trust-api when the trust is offline", () => {
        const t = makeTrust({
            last_heartbeat: secondsAgo(301),
            services: healthyServices(),
            services_updated_at: secondsAgo(5)
        });

        expect(failingFromServices(deriveServices(t, NOW))).toEqual([{
            text: "trust-api down",
            status: "down"
        }]);
    });

    it("lists down services before degraded ones using display labels", () => {
        const services = healthyServices();
        services.omop.status = "degraded";
        services.xnat.status = "down";
        const t = makeTrust({
            services,
            services_updated_at: secondsAgo(5)
        });

        expect(failingFromServices(deriveServices(t, NOW))).toEqual([
            {
                text: "XNAT down",
                status: "down"
            },
            {
                text: "OMOP degraded",
                status: "degraded"
            }
        ]);
    });
});

describe("heartbeatText", () => {
    it("formats never/seconds/minutes/hours/days", () => {
        expect(heartbeatText(null, NOW)).toBe("never");
        expect(heartbeatText(secondsAgo(6), NOW)).toBe("6s ago");
        expect(heartbeatText(secondsAgo(120), NOW)).toBe("2m ago");
        expect(heartbeatText(secondsAgo(7200), NOW)).toBe("2h ago");
        expect(heartbeatText(secondsAgo(200_000), NOW)).toBe("2d ago");
    });

    it("renders never for an unparseable timestamp instead of NaN", () => {
        expect(heartbeatText("not-a-timestamp", NOW)).toBe("never");
    });
});
