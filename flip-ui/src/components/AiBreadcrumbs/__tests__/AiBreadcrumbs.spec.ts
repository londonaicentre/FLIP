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
import { describe, expect, test } from "vitest";

import AiBreadcrumbs from "../AiBreadcrumbs.vue";

const stubs = {
    "router-link": {
        props: ["to"],
        template: "<a><slot /></a>"
    }
};

describe("AiBreadcrumbs", () => {
    test("renders the parent pages and the current page", () => {
        const comp = mount(AiBreadcrumbs, {
            props: {
                pages: [{
                    name: "Projects",
                    path: "/projects"
                }],
                current: { name: "My Project" }
            },
            global: {
                stubs,
                directives: { tippy: () => {} }
            },
            attachTo: document.body
        });

        expect(comp.find("#breadcrumbs").exists()).toBe(true);
        expect(comp.find("[data-test='parent-page-text']").text()).toBe("Projects");
        expect(comp.find("[data-test='current-page-text']").text()).toBe("My Project");

        comp.unmount();
    });

    test("falls back to a skeleton while the current page name is unresolved", () => {
        const comp = mount(AiBreadcrumbs, {
            props: {
                pages: [],
                current: {}
            },
            global: {
                stubs,
                directives: { tippy: () => {} }
            },
            attachTo: document.body
        });

        expect(comp.find("[data-test='current-page-text']").exists()).toBe(false);

        comp.unmount();
    });
});
