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
import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, test, vi } from "vitest";

import SecondaryLayout from "../SecondaryLayout.vue";

// SecondaryLayout picks its logo from useDark(); expose a ref we can flip per test.
// vitest hoists vi.mock/vi.hoisted above the imports above, so the mock is active
// before SecondaryLayout (and its `@vueuse/core` import) is evaluated.
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
    return mount(SecondaryLayout, {
        global: {
            stubs: { "router-view": true },
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false
            })]
        }
    });
}

describe("SecondaryLayout", () => {
    beforeEach(() => {
        darkState.ref!.value = false;
    });

    test("Renders component", () => {
        expect(mountLayout().find(".bg-body").exists()).toBe(true);
    });

    test("sizes the page to the dynamic viewport (h-dvh) so mobile browser bars leave no black bands", () => {
        const comp = mountLayout();

        expect(comp.find(".h-dvh").exists()).toBe(true);
        expect(comp.find(".h-screen").exists()).toBe(false);
    });

    test("fades the corner artwork's letterbox-facing cut edges (iOS-scoped via main.css)", () => {
        const comp = mountLayout();

        expect(comp.find(".absolute.top-0.right-0 img.corner-art-fade-top").exists()).toBe(true);
        expect(comp.find(".absolute.bottom-0.left-0 img.corner-art-fade-bottom").exists()).toBe(true);
    });

    test("shows the light AI Centre logo in light mode", () => {
        darkState.ref!.value = false;
        const comp = mountLayout();

        expect(comp.find(`img[src="${LIGHT_LOGO}"]`).exists()).toBe(true);
        expect(comp.find(`img[src="${DARK_LOGO}"]`).exists()).toBe(false);
    });

    test("shows the dark AI Centre logo in dark mode", () => {
        darkState.ref!.value = true;
        const comp = mountLayout();

        expect(comp.find(`img[src="${DARK_LOGO}"]`).exists()).toBe(true);
        expect(comp.find(`img[src="${LIGHT_LOGO}"]`).exists()).toBe(false);
    });
});
