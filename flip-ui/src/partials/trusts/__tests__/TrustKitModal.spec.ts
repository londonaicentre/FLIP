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
import { beforeEach, describe, expect, it, vi } from "vitest";

import TrustKitModal from "@/partials/trusts/TrustKitModal.vue";

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        success: vi.fn(),
        error: vi.fn()
    }
}));

const trust = {
    id: "trust-uuid-123",
    name: "Demo EC2 Trust",
    code: "DEMO",
    region: "London",
    created_at: null,
    trust_api_key: "api-key-abc",
    trust_internal_service_key: "internal-key-def",
    trust_aes_key: "aes-key-ghi",
    trust_aes_kid: "trust-uuid-123",
    fl_kit_slot: "Trust_1",
    fl_kit_slot_number: 1
};

const stubs = {
    TransitionRoot: { template: "<div><slot /></div>" },
    TransitionChild: { template: "<div><slot /></div>" },
    Dialog: { template: "<div><slot /></div>" },
    DialogTitle: { template: "<h3><slot /></h3>" },
    AiDialogOverlay: { template: "<div />" },
    AiButton: { template: "<button @click=\"$emit('click', $event)\"><slot /></button>" }
};

function mountModal() {
    return mount(TrustKitModal, {
        props: {
            dialog: true,
            trust
        },
        global: { stubs }
    });
}

describe("TrustKitModal — copy all credentials", () => {
    let writeText: ReturnType<typeof vi.fn>;

    beforeEach(() => {
        writeText = vi.fn().mockResolvedValue(undefined);
        Object.assign(navigator, { clipboard: { writeText } });
    });

    it("shows every credential together in one monospaced block", () => {
        const wrapper = mountModal();
        const block = wrapper.find("[data-test='all-credentials-block']");
        expect(block.exists()).toBe(true);
        const text = block.text();
        expect(text).toContain("TRUST_API_KEY=api-key-abc");
        expect(text).toContain("TRUST_INTERNAL_SERVICE_KEY=internal-key-def");
        expect(text).toContain("TRUST_AES_KEY_BASE64=aes-key-ghi");
        expect(text).toContain("# TRUST_AES_KID=trust-uuid-123");
        expect(text).toContain("FL_KIT_SLOT=Trust_1");
        expect(text).toContain("FL_KIT_SLOT_NUMBER=1");
        expect(text).toContain("EXPECTED_TRUST_ID=trust-uuid-123");
    });

    it("puts the trust name in the dialog title", () => {
        const wrapper = mountModal();
        expect(wrapper.find("h3").text()).toContain("Demo EC2 Trust kit");
    });

    it("replaces the per-field copy buttons with the single copy-all", () => {
        const wrapper = mountModal();
        expect(wrapper.find("[data-test='copy-api-key-btn']").exists()).toBe(false);
        expect(wrapper.find("[data-test='copy-internal-key-btn']").exists()).toBe(false);
        expect(wrapper.find("[data-test='copy-fl-kit-slot-btn']").exists()).toBe(false);
        expect(wrapper.find("[data-test='copy-expected-trust-id-btn']").exists()).toBe(false);
        expect(wrapper.find("[data-test='copy-all-credentials-btn']").exists()).toBe(true);
    });

    it("copies every credential line with a single button click", async () => {
        const wrapper = mountModal();
        const btn = wrapper.find("[data-test='copy-all-credentials-btn']");
        expect(btn.exists()).toBe(true);
        await btn.trigger("click");
        expect(writeText).toHaveBeenCalledTimes(1);
        const copied = writeText.mock.calls[0][0] as string;
        expect(copied).toContain("TRUST_API_KEY=api-key-abc");
        expect(copied).toContain("TRUST_INTERNAL_SERVICE_KEY=internal-key-def");
        // The AES key exists only in this response — if the modal stops rendering it,
        // the key is minted and lost, and the trust can never use a per-trust key.
        expect(copied).toContain("TRUST_AES_KEY_BASE64=aes-key-ghi");
        // Commented on purpose: enabling it before the hub holds the key breaks
        // decryption in both directions.
        expect(copied).toContain("# TRUST_AES_KID=trust-uuid-123");
        expect(copied).toContain("FL_KIT_SLOT=Trust_1");
        expect(copied).toContain("FL_KIT_SLOT_NUMBER=1");
        expect(copied).toContain("EXPECTED_TRUST_ID=trust-uuid-123");
    });
});
