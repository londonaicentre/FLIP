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
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";

import { ITrustKitDownload, ITrustKitStatus } from "@/services/trust-service";
import { issuedKit } from "@/utils/issued-trust-kit";

import TrustKitSection from "../TrustKitSection.vue";

const { swrvKeys, swrvKeyFns } = vi.hoisted(() => ({
    swrvKeys: [] as (string | null)[],
    swrvKeyFns: [] as (() => string | null)[]
}));
const statusRef = ref<ITrustKitStatus | undefined>(undefined);
const statusError = ref<unknown>(null);
const mutateStatus = vi.fn();

vi.mock("swrv", () => ({
    default: (keyFn: () => string | null) => {
        swrvKeys.push(keyFn());
        swrvKeyFns.push(keyFn);

        return {
            data: statusRef,
            error: statusError,
            mutate: mutateStatus
        };
    }
}));

const mockDownload = vi.fn();
const mockRotate = vi.fn();
vi.mock("@/services/trust-service", async (importOriginal) => ({
    ...await importOriginal<typeof import("@/services/trust-service")>(),
    downloadTrustKit: (...args: unknown[]) => mockDownload(...args),
    rotateTrustKit: (...args: unknown[]) => mockRotate(...args)
}));

const mockSnackbarError = vi.fn();
vi.mock("@/utils/snackbar", () => ({ Snackbar: { error: (...args: unknown[]) => mockSnackbarError(...args) } }));

const TRUST = {
    id: "t1",
    name: "Guy's and St Thomas'",
    code: "GSTT" as string | null
};

const status = (overrides: Partial<ITrustKitStatus> = {}): ITrustKitStatus => ({
    keys_issued: false,
    fl_backend: "nvflare",
    fl_kit_slot: "Trust_1",
    download_count: 0,
    last_download: null,
    update_available: false,
    ...overrides
});

const issued = (overrides: Partial<ITrustKitStatus> = {}): ITrustKitStatus => status({
    keys_issued: true,
    download_count: 1,
    last_download: {
        at: "2026-11-10T16:37:02.000Z",
        kind: "full",
        by: "site.admin@example.com"
    },
    ...overrides
});

const download = (kind: ITrustKitDownload["kind"] = "full"): ITrustKitDownload => ({
    file_name: "flip-trust-kit-GSTT.tar.gz.enc",
    kind,
    passphrase: "amber-otter-quartz",  // pragma: allowlist secret
    content_base64: btoa("kit")
});

const stubs = {
    AiButton: {
        props: ["disabled", "tooltip"],
        // $attrs carries data-test and the click listener.
        inheritAttrs: false,
        template: "<button v-bind=\"$attrs\" :disabled=\"disabled\" :title=\"tooltip\"><slot /></button>"
    },
    AiConfirmModal: {
        props: ["dialog", "continueAction", "confirmationText", "title", "continueButtonText", "typingConfirmation"],
        template: `<div v-if="dialog" data-test="confirm-modal" :data-typing="typingConfirmation">
            <h3>{{ title }}</h3>
            <p data-test="confirm-text"><slot name="confirmation">{{ confirmationText }}</slot></p>
            <button data-test="confirm-modal-btn" @click="continueAction">{{ continueButtonText }}</button>
        </div>`
    }
};

interface IViewer {
    permissions?: string[];
    trustAdminOf?: { id: string; name: string; code: string } | null;
}

const SITE_ADMIN: IViewer = {
    trustAdminOf: {
        id: "t1",
        name: TRUST.name,
        code: "GSTT"
    }
};
const HUB_ADMIN: IViewer = { permissions: ["CanAccessAdminPanel"] };

const mountSection = (viewer: IViewer = SITE_ADMIN, trust = TRUST, compact = false) => mount(TrustKitSection, {
    props: {
        trust,
        compact
    },
    global: {
        plugins: [createTestingPinia({
            createSpy: vi.fn,
            stubActions: false,
            initialState: {
                auth: {
                    user: {
                        permissions: viewer.permissions ?? ["CanCreateProjects"],
                        trustAdminOf: viewer.trustAdminOf ?? null
                    }
                }
            }
        })],
        stubs
    }
});

const confirm = async (wrapper: ReturnType<typeof mountSection>, button: string) => {
    await wrapper.find(`[data-test='${button}']`).trigger("click");
    await wrapper.find("[data-test='confirm-modal-btn']").trigger("click");
    await flushPromises();
};

describe("TrustKitSection", () => {
    const createObjectURL = vi.fn<(blob: Blob) => string>(() => "blob:kit");
    const revokeObjectURL = vi.fn();
    let anchorClick: ReturnType<typeof vi.spyOn>;

    beforeEach(() => {
        vi.clearAllMocks();
        swrvKeys.length = 0;
        swrvKeyFns.length = 0;
        statusRef.value = status();
        statusError.value = null;
        issuedKit.value = null;
        Object.assign(URL, {
            createObjectURL,
            revokeObjectURL
        });
        anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    });

    afterEach(() => {
        anchorClick.mockRestore();
    });

    describe("who sees it", () => {
        it("shows to the trust's own admin and asks the hub for that trust's kit status", () => {
            const wrapper = mountSection(SITE_ADMIN);

            expect(wrapper.find("[data-test='trust-kit-section']").exists()).toBe(true);
            expect(swrvKeys).toEqual(["/trust/t1/kit"]);
        });

        it("shows to a hub admin", () => {
            expect(mountSection(HUB_ADMIN).find("[data-test='trust-kit-section']").exists()).toBe(true);
        });

        // Also the public demo's viewer: no admin permission and no trust (mocks/demo-server.ts).
        it("renders nothing and asks the hub nothing for anyone else", () => {
            const wrapper = mountSection({});

            expect(wrapper.find("[data-test='trust-kit-section']").exists()).toBe(false);
            expect(swrvKeys).toEqual([null]);
        });

        it("is hidden from another trust's admin", () => {
            const wrapper = mountSection({
                trustAdminOf: {
                    id: "t2",
                    name: "Other",
                    code: "OTH"
                }
            });

            expect(wrapper.find("[data-test='trust-kit-section']").exists()).toBe(false);
            expect(swrvKeys).toEqual([null]);
        });
    });

    describe("states", () => {
        it("offers the first download when no keys were issued", () => {
            const wrapper = mountSection();

            expect(wrapper.find("[data-test='kit-status']").text()).toBe("Not yet downloaded");
            expect(wrapper.find("[data-test='kit-download-btn']").exists()).toBe(true);
            expect(wrapper.find("[data-test='kit-rotate-btn']").exists()).toBe(false);
            expect(wrapper.find("[data-test='kit-update-chip']").exists()).toBe(false);
        });

        it("says who downloaded it last and offers new keys once issued", () => {
            statusRef.value = issued();
            const wrapper = mountSection();

            expect(wrapper.find("[data-test='kit-status']").text()).toBe("Downloaded 10 Nov 2026 by site.admin@example.com");
            expect(wrapper.find("[data-test='kit-download-btn']").exists()).toBe(true);
            expect(wrapper.find("[data-test='kit-rotate-btn']").exists()).toBe(true);
        });

        it("names a trust whose keys were issued from the command line", () => {
            statusRef.value = issued({
                download_count: 0,
                last_download: null
            });

            expect(mountSection().find("[data-test='kit-status']").text()).toBe("Keys issued; not downloaded from here");
        });

        it("flags an available update", () => {
            statusRef.value = issued({ update_available: true });

            expect(mountSection().find("[data-test='kit-update-chip']").text()).toBe("Kit update available");
        });

        it("waits for the status before offering anything", () => {
            statusRef.value = undefined;
            const wrapper = mountSection();

            expect(wrapper.find("[data-test='kit-loading']").exists()).toBe(true);
            expect(wrapper.find("[data-test='kit-download-btn']").exists()).toBe(false);
        });

        it("says so when the status could not be loaded", () => {
            statusRef.value = undefined;
            statusError.value = new Error("boom");
            const wrapper = mountSection();

            expect(wrapper.find("[data-test='kit-error']").exists()).toBe(true);
            expect(wrapper.find("[data-test='kit-download-btn']").exists()).toBe(false);
        });

        it("cannot issue new keys for a trust with no code to type", () => {
            statusRef.value = issued();
            const rotate = mountSection(SITE_ADMIN, {
                ...TRUST,
                code: null
            }).find("[data-test='kit-rotate-btn']");

            expect(rotate.attributes("disabled")).toBeDefined();
            expect(rotate.attributes("title")).toContain("no code");
        });
    });

    describe("download", () => {
        it("explains the first kit and that the download is recorded", async () => {
            const wrapper = mountSection();
            await wrapper.find("[data-test='kit-download-btn']").trigger("click");

            const text = wrapper.find("[data-test='confirm-text']").text();
            expect(text).toContain("holds this trust's keys");
            expect(text).toContain("recorded against your account");
            expect(text).not.toContain("only download that carries the keys");
            expect(mockDownload).not.toHaveBeenCalled();
        });

        it("warns a hub admin that the first download is the one with the keys", async () => {
            const wrapper = mountSection(HUB_ADMIN);
            await wrapper.find("[data-test='kit-download-btn']").trigger("click");

            expect(wrapper.find("[data-test='confirm-text']").text()).toContain("only download that carries the keys");
        });

        it("explains that an update leaves the keys alone", async () => {
            statusRef.value = issued();
            const wrapper = mountSection();
            await wrapper.find("[data-test='kit-download-btn']").trigger("click");

            const text = wrapper.find("[data-test='confirm-text']").text();
            expect(text).toContain("does not change the trust's keys");
            expect(text).not.toContain("holds this trust's keys");
        });

        it("saves the file, shows the passphrase once and refreshes the status", async () => {
            mockDownload.mockResolvedValue(download());
            const wrapper = mountSection();

            await confirm(wrapper, "kit-download-btn");

            expect(mockDownload).toHaveBeenCalledWith("t1");
            const blob = createObjectURL.mock.calls[0][0];
            expect(blob.type).toBe("application/octet-stream");
            expect(await blob.text()).toBe("kit");
            expect(anchorClick).toHaveBeenCalledTimes(1);
            expect((anchorClick.mock.contexts[0] as HTMLAnchorElement).download).toBe("flip-trust-kit-GSTT.tar.gz.enc");
            expect(revokeObjectURL).toHaveBeenCalledWith("blob:kit");

            // MainLayout shows it, so the dialog outlives a table row that leaves the page.
            expect(issuedKit.value).toEqual({
                ...download(),
                trustLabel: "GSTT"
            });
            expect(mutateStatus).toHaveBeenCalled();
            expect(wrapper.find("[data-test='confirm-modal']").exists()).toBe(false);
        });

        it("shows the hub's reason when the kit cannot be built", async () => {
            mockDownload.mockRejectedValue({
                isAxiosError: true,
                response: { data: { detail: "This trust has no FL kit slot." } }
            });
            const wrapper = mountSection();

            await confirm(wrapper, "kit-download-btn");

            expect(mockSnackbarError).toHaveBeenCalledWith(expect.objectContaining({ text: "This trust has no FL kit slot." }));
            expect(anchorClick).not.toHaveBeenCalled();
            expect(issuedKit.value).toBeNull();
        });

        it("says the keys may exist when a first download gets no answer, and re-reads the status", async () => {
            mockDownload.mockRejectedValue({
                isAxiosError: true,
                code: "ECONNABORTED"
            });
            const wrapper = mountSection();

            await confirm(wrapper, "kit-download-btn");

            expect(mockSnackbarError).toHaveBeenCalledWith(expect.objectContaining({
                title: "No answer from the hub",
                text: expect.stringContaining("may have issued this trust's keys")
            }));
            expect(mutateStatus).toHaveBeenCalled();
        });

        it("does not say so for an update, which issues no keys", async () => {
            statusRef.value = issued();
            mockDownload.mockRejectedValue({
                isAxiosError: true,
                code: "ECONNABORTED"
            });
            const wrapper = mountSection();

            await confirm(wrapper, "kit-download-btn");

            expect(mockSnackbarError).toHaveBeenCalledWith(expect.objectContaining({ title: "Kit not downloaded" }));
        });
    });

    describe("issue new keys", () => {
        it("asks for the trust code and warns that the trust goes offline", async () => {
            statusRef.value = issued();
            const wrapper = mountSection();
            await wrapper.find("[data-test='kit-rotate-btn']").trigger("click");

            const modal = wrapper.find("[data-test='confirm-modal']");
            expect(modal.attributes("data-typing")).toBe("GSTT");
            expect(modal.text()).toContain("stop working");
            expect(modal.text()).toContain("offline");
        });

        it("sends the trust code, saves the new kit and shows its passphrase", async () => {
            statusRef.value = issued();
            mockRotate.mockResolvedValue(download());
            const wrapper = mountSection();

            await confirm(wrapper, "kit-rotate-btn");

            expect(mockRotate).toHaveBeenCalledWith("t1", "GSTT");
            expect(mockDownload).not.toHaveBeenCalled();
            expect(anchorClick).toHaveBeenCalledTimes(1);
            expect(issuedKit.value).toMatchObject({ kind: "full" });
            expect(mutateStatus).toHaveBeenCalled();
        });
    });
    describe("compact (the Connection Status table cell)", () => {
        it("is the download button alone, and asks the hub nothing until it is clicked", async () => {
            const wrapper = mountSection(HUB_ADMIN, TRUST, true);

            expect(wrapper.find("[data-test='trust-kit-cell']").exists()).toBe(true);
            expect(wrapper.find("[data-test='trust-kit-section']").exists()).toBe(false);
            expect(wrapper.find("[data-test='kit-rotate-btn']").exists()).toBe(false);
            expect(wrapper.find("[data-test='kit-download-btn']").text()).toBe("Download trust kit");
            expect(wrapper.find("[data-test='kit-download-btn']").attributes("aria-label")).toBe("Download the GSTT trust kit");
            expect(swrvKeys).toEqual([null]);

            await wrapper.find("[data-test='kit-download-btn']").trigger("click");

            expect(swrvKeyFns[0]()).toBe("/trust/t1/kit");
            expect(wrapper.find("[data-test='confirm-modal']").exists()).toBe(true);
        });

        it("renders nothing on a row the viewer may not manage", () => {
            const wrapper = mountSection({
                trustAdminOf: {
                    id: "another-trust",
                    name: "Another",
                    code: "OTH"
                }
            }, TRUST, true);

            expect(wrapper.find("[data-test='trust-kit-cell']").exists()).toBe(false);
            expect(swrvKeys).toEqual([null]);
        });

        it("keeps its clicks and keys from the row behind it", async () => {
            const rowClick = vi.fn();
            const rowKey = vi.fn();
            const wrapper = mount({
                components: { TrustKitSection },
                setup: () => ({
                    trust: TRUST,
                    rowClick,
                    rowKey
                }),
                template: "<div @click=\"rowClick\" @keydown=\"rowKey\"><TrustKitSection compact :trust=\"trust\" /></div>"
            }, {
                global: {
                    plugins: [createTestingPinia({
                        createSpy: vi.fn,
                        stubActions: false,
                        initialState: { auth: { user: { permissions: ["CanAccessAdminPanel"] } } }
                    })],
                    stubs
                }
            });

            await wrapper.find("[data-test='kit-download-btn']").trigger("click");
            await wrapper.find("[data-test='kit-download-btn']").trigger("keydown", { key: "Enter" });

            expect(rowClick).not.toHaveBeenCalled();
            expect(rowKey).not.toHaveBeenCalled();
        });

        it("downloads and shows the passphrase like the card does", async () => {
            mockDownload.mockResolvedValue(download("update"));
            statusRef.value = issued();
            const wrapper = mountSection(HUB_ADMIN, TRUST, true);

            await confirm(wrapper, "kit-download-btn");

            expect(mockDownload).toHaveBeenCalledWith("t1");
            expect(anchorClick).toHaveBeenCalledTimes(1);
            expect(issuedKit.value).toMatchObject({ kind: "update" });
        });
    });
});
