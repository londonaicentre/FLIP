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

import RoleBadge from "@/partials/users/RoleBadge.vue";

describe("RoleBadge", () => {
    it("renders the role name", () => {
        const wrapper = mount(RoleBadge, { props: { roleName: "Researcher" } });

        expect(wrapper.text()).toContain("Researcher");
    });

    it("colour-codes the badge by role", () => {
        const admin = mount(RoleBadge, { props: { roleName: "Admin" } });
        const observer = mount(RoleBadge, { props: { roleName: "Observer" } });

        // Admin (magenta) and Observer (gray) must not render the same tone.
        expect(admin.get("[data-test=role-badge]").attributes("style"))
            .not.toBe(observer.get("[data-test=role-badge]").attributes("style"));
    });

    it("renders a tinted status dot", () => {
        const wrapper = mount(RoleBadge, { props: { roleName: "Admin" } });

        expect(wrapper.get("[data-test=role-badge]").find("span").exists()).toBe(true);
    });

    it("applies a denser style when dense is set", () => {
        const normal = mount(RoleBadge, { props: { roleName: "Admin" } });
        const dense = mount(RoleBadge, {
            props: {
                roleName: "Admin",
                dense: true
            }
        });

        expect(dense.get("[data-test=role-badge]").classes()).toContain("text-[11px]");
        expect(normal.get("[data-test=role-badge]").classes()).not.toContain("text-[11px]");
    });
});
