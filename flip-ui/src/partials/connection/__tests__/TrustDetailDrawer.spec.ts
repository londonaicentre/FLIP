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

import { flushPromises, mount, VueWrapper } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";

import { ITrustResponse } from "@/services/trust-service";

import TrustDetailDrawer from "../TrustDetailDrawer.vue";

const now = Date.now();
const seconds = (n: number) => new Date(now - n * 1000).toISOString();

const snapshot = () => ({
    "trust-api": {
        status: "healthy" as const,
        version: "0.3.0",
        response_ms: null
    },
    xnat: {
        status: "down" as const,
        version: "1.10.0",
        response_ms: null
    },
    "imaging-api": {
        status: "healthy" as const,
        version: "0.3.0",
        response_ms: 12
    },
    omop: {
        status: "degraded" as const,
        version: null,
        response_ms: 1400
    },
    dicom: {
        status: "healthy" as const,
        version: null,
        response_ms: 31
    },
    "data-access-api": {
        status: "healthy" as const,
        version: "0.3.0",
        response_ms: 15
    }
});

const degradedTrust = (): ITrustResponse => ({
    id: "t1",
    name: "Guy's and St Thomas'",
    code: "GSTT",
    region: "London",
    last_heartbeat: seconds(6),
    project_count: 3,
    services: snapshot(),
    services_updated_at: seconds(5)
});

const offlineTrust = (): ITrustResponse => ({
    id: "t2",
    name: "Imperial College Healthcare",
    code: "IMP",
    region: "North West London",
    last_heartbeat: seconds(360),
    project_count: 7,
    services: snapshot(),
    services_updated_at: seconds(360)
});

const onlineTrust = (): ITrustResponse => {
    const services = snapshot();
    services.xnat.status = "healthy" as never;
    services.xnat.response_ms = 220 as never;
    services.omop.status = "healthy" as never;
    services.omop.response_ms = 64 as never;

    return {
        ...degradedTrust(),
        services
    };
};

// HeadlessUI's Dialog teleports its content out of the SFC tree, so panel
// content must be queried from the document (AiConfirmModal.spec idiom):
// mount attached to the body, flush, then read document.body.
const q = (sel: string): HTMLElement | null => document.body.querySelector(sel);
const qa = (sel: string): HTMLElement[] => Array.from(document.body.querySelectorAll(sel));

let wrapper: VueWrapper | null = null;

const mountDrawer = async (trust: ITrustResponse | null, show = true) => {
    wrapper = mount(TrustDetailDrawer, {
        attachTo: document.body,
        global: {
            // test/setup.ts shallow-stubs the HeadlessUI Dialog/Transition
            // components globally, which would blank the drawer's slot content.
            // This spec is ABOUT that content, so mount the real machinery.
            stubs: {
                Dialog: false,
                DialogTitle: false,
                TransitionRoot: false,
                TransitionChild: false,
                Teleport: false
            }
        },
        props: {
            trust,
            show
        }
    });
    await flushPromises();

    return wrapper;
};

afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
    document.body.replaceChildren();
});

describe("TrustDetailDrawer", () => {
    it("renders nothing while hidden", async () => {
        await mountDrawer(degradedTrust(), false);
        expect(q("[data-test='drawer-panel']")).toBeNull();
    });

    it("renders header identity: eyebrow, name, code · region, status pill and heartbeat", async () => {
        await mountDrawer(degradedTrust());

        const panel = q("[data-test='drawer-panel']");
        expect(panel).not.toBeNull();
        expect(panel?.textContent).toContain("Trust detail");
        expect(panel?.textContent).toContain("Guy's and St Thomas'");
        expect(panel?.textContent).toContain("GSTT · London");
        expect(panel?.textContent).toContain("Degraded");
        expect(q("[data-test='drawer-heartbeat']")?.textContent).toMatch(/heartbeat \d+s ago/);
    });

    it("lists all six containers in registry order with status chips", async () => {
        await mountDrawer(degradedTrust());

        const rows = qa("[data-test='container-row']");
        expect(rows).toHaveLength(6);
        expect(rows.map(r => r.getAttribute("data-service"))).toEqual([
            "trust-api",
            "xnat",
            "imaging-api",
            "omop",
            "dicom",
            "data-access-api"
        ]);
        const chip = (row: HTMLElement) => row.querySelector("[data-test='container-chip']")?.textContent?.trim();
        expect(chip(rows[1])).toBe("Down");
        expect(chip(rows[3])).toBe("Degraded");
        expect(chip(rows[0])).toBe("Healthy");
    });

    it("shows role text and a version · response meta line with em-dash fallbacks", async () => {
        await mountDrawer(degradedTrust());
        const rows = qa("[data-test='container-row']");
        const meta = (row: HTMLElement) => row.querySelector("[data-test='container-meta']")?.textContent ?? "";

        // imaging-api: healthy with version + latency.
        expect(rows[2].textContent).toContain("DICOM query / retrieve");
        expect(meta(rows[2])).toContain("0.3.0");
        expect(meta(rows[2])).toContain("12 ms");
        // xnat is down: version still shown, response falls back to an em dash.
        expect(meta(rows[1])).toContain("1.10.0");
        expect(meta(rows[1])).toContain("—");
        // omop has no version: em dash for version, latency shown.
        expect(meta(rows[3])).toContain("—");
        expect(meta(rows[3])).toContain("1400 ms");
    });

    it("tints each container icon by its status", async () => {
        await mountDrawer(degradedTrust());
        const rows = qa("[data-test='container-row']");
        const icon = (row: HTMLElement) => row.querySelector("svg");

        expect(icon(rows[0])?.getAttribute("data-icon")).toBe("ph:plugs-connected-duotone");
        expect(icon(rows[0])?.classList).toContain("text-emerald-600");
        expect(icon(rows[1])?.getAttribute("data-icon")).toBe("ph:images-duotone");
        expect(icon(rows[1])?.classList).toContain("text-red-500");
        expect(icon(rows[3])?.classList).toContain("text-amber-500");
    });

    it("renders No data chips when the trust is offline and the snapshot stale", async () => {
        await mountDrawer(offlineTrust());

        const chips = qa("[data-test='container-chip']").map(c => c.textContent?.trim());
        // trust-api chip derives from heartbeat age → Down; the rest are No data.
        expect(chips[0]).toBe("Down");
        for (const chip of chips.slice(1)) {
            expect(chip).toBe("No data");
        }
    });

    it("shows the degraded issue banner with the affected-container count", async () => {
        await mountDrawer(degradedTrust());

        // xnat down + omop degraded → 2 containers affecting service.
        expect(q("[data-test='drawer-banner']")?.textContent).toContain("2 containers affecting service.");
    });

    it("shows the offline issue banner when trust-api is unreachable", async () => {
        await mountDrawer(offlineTrust());

        expect(q("[data-test='drawer-banner']")?.textContent).toContain(
            "Core trust-api is unreachable — no data can be collected from this Trust."
        );
    });

    it("hides the issue banner when the trust is online", async () => {
        await mountDrawer(onlineTrust());

        expect(q("[data-test='drawer-banner']")).toBeNull();
        expect(q("[data-test='drawer-panel']")?.textContent).toContain("Online");
    });

    it("labels the container section with the count", async () => {
        await mountDrawer(degradedTrust());
        expect(q("[data-test='drawer-panel']")?.textContent).toContain("Containers · 6");
    });

    it("emits close from the ✕ button", async () => {
        const w = await mountDrawer(degradedTrust());

        q("[data-test='drawer-close']")?.click();
        await flushPromises();
        expect(w.emitted("close")).toHaveLength(1);
    });

    it("keeps rendering content through the close transition when the page nulls the trust", async () => {
        // The page clears selectedTrustId on @close, so `trust` and `show` flip in
        // the same tick while the 250ms leave transition still renders the panel.
        // The panel div must stay unconditional (a v-if would hand HeadlessUI's
        // TransitionChild a comment vnode -> render error) and the cached trust
        // keeps the slide-out from going blank.
        const w = await mountDrawer(degradedTrust());
        expect(q("[data-test='drawer-panel']")).not.toBeNull();

        await w.setProps({
            trust: null,
            show: false
        });
        await flushPromises();
        // No render error thrown; the leaving panel still shows the trust's content.
        expect(q("[data-test='drawer-panel']")?.textContent).toContain("Guy's and St Thomas'");
    });

    it("reopens cleanly after a close on the same mounted component", async () => {
        const w = await mountDrawer(degradedTrust());

        await w.setProps({ show: false });
        await flushPromises();
        await w.setProps({ show: true });
        await flushPromises();

        expect(q("[data-test='drawer-panel']")).not.toBeNull();
        expect(qa("[data-test='container-row']")).toHaveLength(6);
    });

    it("renders nothing before the first open", async () => {
        await mountDrawer(null, false);
        expect(q("[data-test='drawer-panel']")).toBeNull();
    });
});
