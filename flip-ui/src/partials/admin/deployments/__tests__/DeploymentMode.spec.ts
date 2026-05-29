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

import { mountComponent } from "@test/helper";
import { describe, expect, it } from "vitest";

import DeploymentMode from "@/partials/admin/deployments/DeploymentMode.vue";

const confirmModalStub = { template: "<div data-test=\"confirm-modal-stub\"><slot name=\"confirmation\" /></div>" };

describe("DeploymentMode", () => {
    it("wires the confirmation slot into AiConfirmModal with the deployment-mode warning", () => {
        const wrapper = mountComponent(DeploymentMode, { global: { stubs: { AiConfirmModal: confirmModalStub } } });

        const html = wrapper.html();

        expect(html).toContain("deployment mode");
        expect(html).toContain("disable core functionality across the platform");
    });
});
