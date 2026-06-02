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

import DeploymentMode from "@/partials/admin/deployments/DeploymentMode.vue";
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
    }
};

function mountDeploymentMode(options: {
    deploymentMode?: boolean;
    bannerPresent?: boolean;
} = {}) {
    const { deploymentMode = false, bannerPresent = true } = options;

    return mount(DeploymentMode, {
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false,
                initialState: {
                    siteDetails: {
                        banner: bannerPresent ? {
                            enabled: false,
                            message: "x"
                        } : undefined,
                        deploymentMode
                    }
                }
            })],
            stubs
        }
    });
}

describe("DeploymentMode", () => {
    it("wires the confirmation slot into AiConfirmModal with the deployment-mode warning", () => {
        const wrapper = mountDeploymentMode();
        const html = wrapper.html();

        expect(html).toContain("deployment mode");
        expect(html).toContain("disable core functionality across the platform");
    });

    it("confirm() opens the dialog when deployment mode is currently off", async () => {
        const wrapper = mountDeploymentMode({ deploymentMode: false });
        const modal = wrapper.findComponent({ name: "AiConfirmModal" });
        expect(modal.props("dialog")).toBe(false);

        const toggleBtn = wrapper.findAll("button").find(b => b.text().includes("Enable Deployment Mode"))!;
        await toggleBtn.trigger("click");

        expect(modal.props("dialog")).toBe(true);
    });

    it("confirm() bypasses the dialog and toggles immediately when deployment mode is already on", async () => {
        const wrapper = mountDeploymentMode({ deploymentMode: true });
        const store = useSiteDetailsStore();
        const modal = wrapper.findComponent({ name: "AiConfirmModal" });

        const toggleBtn = wrapper.findAll("button").find(b => b.text().includes("Disable Deployment Mode"))!;
        await toggleBtn.trigger("click");
        await flushPromises();

        // Dialog stays closed; the store action fires immediately with the inverted value.
        expect(modal.props("dialog")).toBe(false);
        expect(store.updateDeploymentMode).toHaveBeenCalledWith(false);
    });

    it("AiConfirmModal close-modal flips confirmDialog back to false", async () => {
        const wrapper = mountDeploymentMode({ deploymentMode: false });
        const toggleBtn = wrapper.findAll("button").find(b => b.text().includes("Enable Deployment Mode"))!;
        await toggleBtn.trigger("click");
        const modal = wrapper.findComponent({ name: "AiConfirmModal" });
        expect(modal.props("dialog")).toBe(true);

        await modal.vm.$emit("close-modal");
        expect(modal.props("dialog")).toBe(false);
    });

    it("toggleDeploymentMode() persists the inverted flag via updateDeploymentMode and closes the dialog", async () => {
        const wrapper = mountDeploymentMode({ deploymentMode: false });
        const store = useSiteDetailsStore();
        const modal = wrapper.findComponent({ name: "AiConfirmModal" });

        // Invoke continueAction directly to bypass the dialog's button UI.
        await modal.props("continueAction")();
        await flushPromises();

        expect(store.updateDeploymentMode).toHaveBeenCalledWith(true);
        expect(modal.props("dialog")).toBe(false);
    });
});
