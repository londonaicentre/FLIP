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
import { describe, expect, it, vi } from "vitest";

import AddTrustModal from "@/partials/trusts/AddTrustModal.vue";
import { addTrustSchema } from "@/partials/trusts/addTrustSchema";

vi.mock("@/services/admin-trusts-service", () => ({ createAdminTrust: vi.fn() }));

// Pass-through stubs for the headless-ui dialog chrome so the form fields render inline.
const passthrough = (tag = "div") => ({ template: `<${tag}><slot /></${tag}>` });

function mountModal() {
    return mount(AddTrustModal, {
        props: { dialog: true },
        global: {
            plugins: [createTestingPinia({ createSpy: vi.fn })],
            stubs: {
                TransitionRoot: passthrough(),
                TransitionChild: passthrough(),
                Dialog: passthrough(),
                DialogTitle: passthrough("h3"),
                AiDialogOverlay: passthrough()
            }
        }
    });
}

describe("addTrustSchema", () => {
    it("rejects a missing or blank code (code is required)", async () => {
        await expect(addTrustSchema.validate({ name: "Guy's Trust" })).rejects.toThrow();
        await expect(addTrustSchema.validate({
            name: "Guy's Trust",
            code: ""
        })).rejects.toThrow();
        await expect(addTrustSchema.validate({
            name: "Guy's Trust",
            code: "   "
        })).rejects.toThrow();
    });

    it("rejects a code with characters outside [A-Za-z0-9_-]", async () => {
        await expect(addTrustSchema.validate({
            name: "Guy's Trust",
            code: "G S"
        })).rejects.toThrow();
    });

    it("accepts a name with arbitrary characters and a well-formed code", async () => {
        const value = await addTrustSchema.validate({
            name: "Guy's & St Thomas' NHS Foundation Trust",
            code: "GSTT"
        });
        expect(value.code).toBe("GSTT");
    });

    it("still requires a non-empty name", async () => {
        await expect(addTrustSchema.validate({ code: "GSTT" })).rejects.toThrow();
    });
});

describe("AddTrustModal rendering", () => {
    it("renders the code field bound to the `code` form field and marked required", () => {
        const codeInput = mountModal().find("[data-test=trust-code-field]");

        expect(codeInput.exists()).toBe(true);
        expect(codeInput.attributes("name")).toBe("code");
        // The `:required` flag drives the native required attribute on the input.
        expect(codeInput.attributes("required")).toBeDefined();
    });

    it("describes the code (not the name) as the identifier, with the charset hint", () => {
        const text = mountModal().text();

        // The code field hint calls out the stable identifier + allowed charset.
        expect(text).toContain("Letters, digits, underscore, hyphen.");
        // The name field no longer mis-describes itself as the auth identifier.
        expect(text).not.toContain("authenticate with the hub");
    });

    it("spaces the footer buttons responsively so they stack cleanly on mobile", () => {
        const wrapper = mountModal();

        // AiButton puts its class attribute on a wrapper div around the native button.
        const confirm = wrapper.find("[data-test=confirm-create-trust-btn]").element.parentElement;
        const cancel = wrapper.find("[data-test=close-add-trust-modal-btn]").element.parentElement;

        // The left margin between the row buttons must only apply once the footer
        // becomes a row (sm+); on mobile a bare ml-2 pushes the full-width primary
        // button past the footer's right edge.
        expect(confirm?.className).toContain("sm:ml-2");
        expect(confirm?.className).not.toMatch(/(?:^|\s)ml-2(?:\s|$)/);
        // Stacked buttons need a vertical gap, collapsed again in the sm+ row.
        expect(cancel?.className).toContain("mt-2");
        expect(cancel?.className).toContain("sm:mt-0");
    });
});
