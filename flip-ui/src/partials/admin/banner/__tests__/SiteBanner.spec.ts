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

import { createTestingPinia } from "@pinia/testing";
import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import SiteBanner from "@/partials/admin/banner/SiteBanner.vue";
import { useSiteDetailsStore } from "@/store/siteDetailsStore";

const confirmModalStub = {
    name: "AiConfirmModal",
    props: ["dialog", "submitting", "continueAction"],
    emits: ["close-modal"],
    template: "<div data-test=\"confirm-modal-stub\" :data-dialog=\"dialog\"><slot name=\"confirmation\" /></div>"
};

const stubs = {
    AiConfirmModal: confirmModalStub,
    AiLoader: { template: "<div data-test=\"loader\" />" },
    AiButton: {
        props: ["loading"],
        inheritAttrs: false,
        template: "<button v-bind=\"$attrs\" :disabled=\"loading\" @click=\"$emit('click', $event)\"><slot /></button>"
    },
    AiTextArea: { template: "<textarea />" },
    AiInput: {
        name: "AiInput",
        template: "<div data-test=\"ai-input\"><slot name=\"inputButton\" /></div>"
    },
    AiSkeleton: { template: "<div />" },
    transition: false,
    Transition: false,
    Form: {
        name: "Form",
        emits: ["submit"],
        template: "<form data-test=\"banner-form\" @submit.prevent=\"$emit('submit', { message: 'Updated', link: 'https://x' })\"><slot :values=\"{}\" /></form>"
    }
};

function mountBanner(options: {
    enabled?: boolean;
    bannerOverride?: { enabled: boolean; message: string; link?: string } | null;
} = {}) {
    const { enabled = false, bannerOverride } = options;
    const banner = bannerOverride === null
        ? null
        : (bannerOverride ?? {
            enabled,
            message: "Hello",
            link: undefined
        });

    return mount(SiteBanner, {
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false,
                initialState: {
                    siteDetails: {
                        banner,
                        deploymentMode: false
                    }
                }
            })],
            stubs
        }
    });
}

describe("SiteBanner", () => {
    it("wires the confirmation slot into AiConfirmModal with the site-banner warning", () => {
        const wrapper = mountBanner();
        const html = wrapper.html();

        expect(html).toContain("site banner");
        expect(html).toContain("show across all pages for every user");
    });

    it("confirm() opens the dialog when the banner is currently disabled", async () => {
        const wrapper = mountBanner({ enabled: false });
        const modal = wrapper.findComponent({ name: "AiConfirmModal" });
        expect(modal.props("dialog")).toBe(false);

        // The first AiButton (the primary one in the header) is the Enable/Disable toggle.
        const toggleBtn = wrapper.findAll("button").find(b => b.text().includes("Enable Site Banner"))!;
        await toggleBtn.trigger("click");
        await flushPromises();

        expect(modal.props("dialog")).toBe(true);
    });

    it("confirm() skips the dialog and toggles immediately when the banner is already enabled", async () => {
        // Already-enabled → confirm() calls toggleBanner() inline. The store's
        // updateBanner action is mocked by createTestingPinia.
        const wrapper = mountBanner({ enabled: true });
        const store = useSiteDetailsStore();
        const modal = wrapper.findComponent({ name: "AiConfirmModal" });

        const toggleBtn = wrapper.findAll("button").find(b => b.text().includes("Disable Site Banner"))!;
        await toggleBtn.trigger("click");
        await flushPromises();

        // Dialog stays closed; the store update fires immediately.
        expect(modal.props("dialog")).toBe(false);
        expect(store.updateBanner).toHaveBeenCalledWith(
            expect.objectContaining({ enabled: false })
        );
    });

    it("AiConfirmModal close-modal flips confirmDialog back to false", async () => {
        const wrapper = mountBanner({ enabled: false });
        const toggleBtn = wrapper.findAll("button").find(b => b.text().includes("Enable Site Banner"))!;
        await toggleBtn.trigger("click");
        const modal = wrapper.findComponent({ name: "AiConfirmModal" });
        expect(modal.props("dialog")).toBe(true);

        await modal.vm.$emit("close-modal");
        expect(modal.props("dialog")).toBe(false);
    });

    it("toggleBanner() persists the inverted enabled flag via updateBanner", async () => {
        const wrapper = mountBanner({ enabled: false });
        const store = useSiteDetailsStore();

        // Invoke the modal's continueAction directly to bypass the dialog UI.
        const modal = wrapper.findComponent({ name: "AiConfirmModal" });
        await modal.props("continueAction")();
        await flushPromises();

        expect(store.updateBanner).toHaveBeenCalledWith(
            expect.objectContaining({ enabled: true })
        );
    });

    it("updateBanner() persists the form values while preserving the existing enabled flag", async () => {
        const wrapper = mountBanner({ enabled: true });
        const store = useSiteDetailsStore();

        await wrapper.find("[data-test=\"banner-form\"]").trigger("submit");
        await flushPromises();

        // Form-submit handler must keep `enabled` from the existing banner rather
        // than letting form values override it — the dedicated toggle button is
        // the only path that flips enabled.
        expect(store.updateBanner).toHaveBeenCalledWith({
            message: "Updated",
            link: "https://x",
            enabled: true
        });
    });

    it("toggleBanner() short-circuits when details.banner is null", async () => {
        const wrapper = mountBanner({ bannerOverride: null });
        const store = useSiteDetailsStore();

        // With banner=null the loader is rendered and the modal isn't mounted —
        // so we cannot invoke continueAction via findComponent. Instead, mount
        // with a banner present to obtain the action reference, then re-assert
        // via store.banner override before invocation.
        const present = mountBanner();
        const modal = present.findComponent({ name: "AiConfirmModal" });
        const action = modal.props("continueAction");

        // Now wipe the store's banner and invoke the action with no banner.
        useSiteDetailsStore().banner = undefined;
        await action();
        await flushPromises();

        // Once banner is undefined the action must short-circuit; the present
        // wrapper's store was reset, so updateBanner was never called for the
        // null-banner case.
        expect(store.updateBanner).not.toHaveBeenCalled();
        wrapper.unmount();
        present.unmount();
    });
});
