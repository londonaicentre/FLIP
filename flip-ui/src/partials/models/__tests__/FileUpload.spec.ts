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

import FileUpload from "@/partials/models/FileUpload.vue";

const alertStub = { template: "<div data-test=\"alert-stub\"><slot /></div>" };

function mountFileUpload(options: {
    permissions?: string[];
    requiredFiles?: string[];
    jobType?: string;
} = {}) {
    const {
        permissions = ["CanCreateProjects"],
        requiredFiles = ["trainer.py", "config.json"],
        jobType = "standard"
    } = options;

    return mount(FileUpload, {
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false,
                    initialState: {
                        auth: {
                            user: {
                                username: "testuser",
                                userId: "1",
                                attributes: {
                                    sub: "1",
                                    email: "t@e.co"
                                },
                                permissions
                            },
                            signInStep: "DONE"
                        }
                    }
                })
            ],
            renderStubDefaultSlot: true,
            stubs: { AiAlert: alertStub }
        },
        props: {
            requiredFiles,
            jobType
        }
    });
}

describe("FileUpload viewer-aware rendering", () => {
    it("hides the upload zone when the user lacks CanCreateProjects", () => {
        const wrapper = mountFileUpload({ permissions: [] });

        expect(wrapper.find("[data-test=upload-file-input]").exists()).toBe(false);
        expect(wrapper.find("[data-test=alert-stub]").exists()).toBe(false);
    });

    it("renders the upload zone for users with CanCreateProjects", () => {
        const wrapper = mountFileUpload();

        expect(wrapper.find("[data-test=upload-file-input]").exists()).toBe(true);
    });

    // The "alert with jobType / required files" markup lives in Training.vue,
    // not FileUpload.vue — Training.spec covers it. These cases used to
    // mount FileUpload but assert against Training's alert; deleted as
    // duplicates of the Training-side coverage.

    it("emits newFiles when the file input changes", async () => {
        const wrapper = mountFileUpload();
        const file = new File(["x"], "trainer.py");
        const input = wrapper.find<HTMLInputElement>("[data-test=upload-file-input]");

        Object.defineProperty(input.element, "files", {
            value: [file],
            configurable: true
        });
        await input.trigger("change");

        const events = wrapper.emitted("newFiles");
        expect(events).toBeTruthy();
        expect(events![0][0]).toContain(file);
    });
});
