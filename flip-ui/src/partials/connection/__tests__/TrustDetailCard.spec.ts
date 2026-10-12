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

import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import { h } from "vue";

import { IServiceHealth, ITrustResponse } from "@/services/trust-service";
import { deriveTrust } from "@/utils/connection-health";

import TrustDetailCard from "../TrustDetailCard.vue";

const now = Date.now();
const seconds = (n: number) => new Date(now - n * 1000).toISOString();

const services = (xnat: IServiceHealth["status"]): Record<string, IServiceHealth> => ({
    "trust-api": {
        status: "healthy",
        version: "0.3.0",
        response_ms: null
    },
    xnat: {
        status: xnat,
        version: "1.10.0",
        response_ms: null
    },
    "imaging-api": {
        status: "healthy",
        version: "0.3.0",
        response_ms: 12
    },
    omop: {
        status: "healthy",
        version: null,
        response_ms: 64
    },
    dicom: {
        status: "healthy",
        version: null,
        response_ms: 31
    },
    "data-access-api": {
        status: "healthy",
        version: "0.3.0",
        response_ms: 15
    }
});

const trust = (xnat: IServiceHealth["status"] = "healthy"): ITrustResponse => ({
    id: "t1",
    name: "Guy's and St Thomas'",
    code: "GSTT",
    region: "London",
    last_heartbeat: seconds(6),
    project_count: 3,
    services: services(xnat),
    services_updated_at: seconds(5)
});

// The card is the drawer's body without the Dialog, so it mounts plainly — no teleport, no stubs.
describe("TrustDetailCard", () => {
    it("titles the card with the trust's name in a heading by default", () => {
        const wrapper = mount(TrustDetailCard, { props: { trust: deriveTrust(trust()) } });

        expect(wrapper.find("h2").text()).toBe("Guy's and St Thomas'");
        expect(wrapper.text()).toContain("GSTT · London");
        expect(wrapper.find("[data-test='drawer-heartbeat']").text()).toMatch(/heartbeat \d+s ago/);
    });

    it("lists one row per container and no banner when every service is healthy", () => {
        const wrapper = mount(TrustDetailCard, { props: { trust: deriveTrust(trust()) } });

        expect(wrapper.findAll("[data-test='container-row']")).toHaveLength(6);
        expect(wrapper.find("[data-test='drawer-banner']").exists()).toBe(false);
    });

    it("shows the issue banner when a service is down", () => {
        const wrapper = mount(TrustDetailCard, { props: { trust: deriveTrust(trust("down")) } });

        expect(wrapper.find("[data-test='drawer-banner']").exists()).toBe(true);
    });

    it("lets the host replace the title and fill the header's action slot", () => {
        const wrapper = mount(TrustDetailCard, {
            props: { trust: deriveTrust(trust()) },
            slots: {
                title: () => h("h3", { "data-test": "custom-title" }, "Custom"),
                actions: () => h("button", { "data-test": "custom-action" }, "x")
            }
        });

        expect(wrapper.find("[data-test='custom-title']").exists()).toBe(true);
        expect(wrapper.find("h2").exists()).toBe(false);
        expect(wrapper.find("[data-test='custom-action']").exists()).toBe(true);
    });
});
