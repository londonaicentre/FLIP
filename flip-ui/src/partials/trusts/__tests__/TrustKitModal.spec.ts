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
import { ITrustKitDownload } from "@/services/trust-service";

const mockSaveKitFile = vi.fn();
vi.mock("@/utils/issued-trust-kit", () => ({ saveKitFile: (...args: unknown[]) => mockSaveKitFile(...args) }));

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        success: vi.fn(),
        error: vi.fn()
    }
}));

const kit = (kind: ITrustKitDownload["kind"] = "full"): ITrustKitDownload => ({
    file_name: "flip-trust-kit-DEMO.tar.gz.enc",
    kind,
    passphrase: "amber-otter-quartz",  // pragma: allowlist secret
    content_base64: btoa("kit")
});

const stubs = {
    TransitionRoot: { template: "<div><slot /></div>" },
    TransitionChild: { template: "<div><slot /></div>" },
    Dialog: { template: "<div><slot /></div>" },
    DialogTitle: { template: "<h3><slot /></h3>" },
    AiDialogOverlay: { template: "<div />" },
    AiButton: {
        props: ["disabled"],
        // $attrs carries data-test and the click listener.
        inheritAttrs: false,
        template: "<button v-bind=\"$attrs\" :disabled=\"disabled\"><slot /></button>"
    }
};

const mountModal = (issued: ITrustKitDownload | null = kit()) => mount(TrustKitModal, {
    props: {
        kit: issued,
        trustLabel: "DEMO"
    },
    global: { stubs }
});

describe("TrustKitModal", () => {
    let writeText: ReturnType<typeof vi.fn>;

    beforeEach(() => {
        writeText = vi.fn().mockResolvedValue(undefined);
        Object.assign(navigator, { clipboard: { writeText } });
    });

    it("shows the file name, the passphrase and the install command for that file", () => {
        const wrapper = mountModal();

        expect(wrapper.find("h3").text()).toContain("DEMO kit");
        expect(wrapper.find("[data-test='kit-warning']").text()).toContain("You won't see this again.");
        expect(wrapper.find("[data-test='kit-file-name']").text()).toBe("flip-trust-kit-DEMO.tar.gz.enc");
        expect(wrapper.find("[data-test='kit-passphrase']").text()).toBe("amber-otter-quartz");
        expect(wrapper.find("[data-test='kit-install-command']").text())
            .toBe("make -C trust install-kit FILE=flip-trust-kit-DEMO.tar.gz.enc");
    });

    it("copies the passphrase", async () => {
        const wrapper = mountModal();

        await wrapper.find("[data-test='copy-passphrase-btn']").trigger("click");

        expect(writeText).toHaveBeenCalledWith("amber-otter-quartz");
    });

    it("saves the file again, for a browser that blocked or lost the first save", async () => {
        const wrapper = mountModal();

        await wrapper.find("[data-test='kit-save-again-btn']").trigger("click");

        expect(mockSaveKitFile).toHaveBeenCalledWith(kit());
    });

    it("tells a new site to send its public IP, on a full kit only", () => {
        expect(mountModal(kit("full")).find("[data-test='kit-public-ip-note']").exists()).toBe(true);
        expect(mountModal(kit("update")).find("[data-test='kit-public-ip-note']").exists()).toBe(false);
    });

    it("closes only once the passphrase is confirmed saved", async () => {
        const wrapper = mountModal();
        const close = wrapper.find("[data-test='kit-done-btn']");

        expect(close.attributes("disabled")).toBeDefined();

        await wrapper.find("[data-test='kit-saved-checkbox']").setValue(true);
        expect(close.attributes("disabled")).toBeUndefined();

        await close.trigger("click");
        expect(wrapper.emitted("closeModal")).toHaveLength(1);
    });

    it("asks for the tick again for the next kit", async () => {
        const wrapper = mountModal();
        await wrapper.find("[data-test='kit-saved-checkbox']").setValue(true);

        await wrapper.setProps({ kit: null });
        await wrapper.setProps({ kit: kit("update") });

        expect(wrapper.find("[data-test='kit-done-btn']").attributes("disabled")).toBeDefined();
    });
});
