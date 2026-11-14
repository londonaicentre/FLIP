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

import DeploymentMode from "@/pages/DeploymentMode.vue";

describe("Platform Updating page", () => {
    it("tells the user the platform is being updated and who to contact", () => {
        const text = mount(DeploymentMode).find("[data-test=deployment-mode-page]").text();

        expect(text).toContain("We're currently updating the platform");
        expect(text).toContain("Please contact the service desk");
    });
});
