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

import UserAvatar from "@/partials/users/UserAvatar.vue";

describe("UserAvatar", () => {
    it("renders initials derived from the user's name", () => {
        const wrapper = mount(UserAvatar, {
            props: {
                name: "Priya Raghavan",
                email: "p.raghavan@kcl.ac.uk",
                roleName: "Researcher"
            }
        });

        expect(wrapper.text()).toBe("PR");
    });

    it("strips honorifics from the name", () => {
        const wrapper = mount(UserAvatar, {
            props: {
                name: "Dr Amara Okonkwo",
                email: "a.okonkwo@gstt.nhs.uk",
                roleName: "Admin"
            }
        });

        expect(wrapper.text()).toBe("AO");
    });

    it("falls back to the email when no name is given", () => {
        const wrapper = mount(UserAvatar, { props: { email: "tomas.ribeiro@nhs.uk" } });

        expect(wrapper.text()).toBe("TR");
    });

    it("sizes the avatar from the size prop", () => {
        const wrapper = mount(UserAvatar, {
            props: {
                name: "Test User",
                size: 48
            }
        });

        const style = wrapper.get("[data-test=user-avatar]").attributes("style") ?? "";
        expect(style).toContain("width: 48px");
        expect(style).toContain("height: 48px");
    });

    it("colour-codes the avatar by role", () => {
        const admin = mount(UserAvatar, {
            props: {
                name: "A B",
                roleName: "Admin"
            }
        });
        const observer = mount(UserAvatar, {
            props: {
                name: "A B",
                roleName: "Observer"
            }
        });

        expect(admin.get("[data-test=user-avatar]").attributes("style"))
            .not.toBe(observer.get("[data-test=user-avatar]").attributes("style"));
    });
});
