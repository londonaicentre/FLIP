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
import { createPinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AuthLayout from "../AuthLayout.vue";

// AuthLayout picks its logo from useDark(); expose a ref we can flip per test.
// vitest hoists vi.mock/vi.hoisted above the imports above, so the mock is active
// before AuthLayout (and its `@vueuse/core` import) is evaluated.
const darkState = vi.hoisted(() => ({ ref: null as null | { value: boolean } }));

vi.mock("@vueuse/core", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@vueuse/core")>();
    const { ref } = await import("vue");
    darkState.ref = ref(false);

    return {
        ...actual,
        useDark: () => darkState.ref
    };
});

const LIGHT_LOGO = "/images/aicentre-logo-transparent.webp";
const DARK_LOGO = "/images/aicentre-logo-transparent-dark.webp";

function mountLayout() {
    return mountComponent(AuthLayout, {
        global: {
            mocks:
                { useRoute: () => ({ name: "SomeName" }) },
            plugins: [createPinia()]
        }
    });
}

describe("Auth Layout", () => {
    beforeEach(() => {
        darkState.ref!.value = false;
    });

    it("Renders component", () => {
        const component = mountLayout();

        expect(component.find(".bg-body").exists()).toBe(true);
        expect(component.find(".absolute.top-0.right-0 img").exists()).toBe(true);
        expect(component.find(".absolute.bottom-0.left-0 img").exists()).toBe(true);
        expect(component.find("img[alt=\"NHS logo\"]").exists()).toBe(false);
    });

    it("shows the light AI Centre logo in light mode", () => {
        darkState.ref!.value = false;
        const component = mountLayout();

        expect(component.find(`img[src="${LIGHT_LOGO}"]`).exists()).toBe(true);
        expect(component.find(`img[src="${DARK_LOGO}"]`).exists()).toBe(false);
    });

    it("shows the dark AI Centre logo in dark mode", () => {
        darkState.ref!.value = true;
        const component = mountLayout();

        expect(component.find(`img[src="${DARK_LOGO}"]`).exists()).toBe(true);
        expect(component.find(`img[src="${LIGHT_LOGO}"]`).exists()).toBe(false);
    });
});
