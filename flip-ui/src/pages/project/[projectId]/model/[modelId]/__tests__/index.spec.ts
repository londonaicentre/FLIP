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
import { beforeEach, describe, expect, it, vi } from "vitest";
import { reactive, ref } from "vue";

import { FileUploadStatus } from "@/interfaces/model/types";
import type { IModelDashboard } from "@/services/model-service";

const mockRoute = reactive({
    name: "Model",
    fullPath: "/project/test-project/model/test-model",
    path: "/project/test-project/model/test-model",
    params: {
        projectId: "test-project",
        modelId: "test-model"
    } as Record<string, string>
});

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => mockRoute
    };
});

const mockSwrvData = ref<IModelDashboard | undefined>(undefined);
const mockSwrvError = ref<Error | null>(null);
const mutateMock = vi.fn();

vi.mock("swrv", () => ({
    default: () => ({
        data: mockSwrvData,
        mutate: mutateMock,
        error: mockSwrvError
    })
}));

vi.mock("@/composables/useErrorHandler", () => ({ default: vi.fn() }));

vi.mock("@/router", () => ({ routeChange: { viewProject: vi.fn() } }));

const resolveModelConfigStateMock = vi.fn();

vi.mock("@/services/file-service", () => ({ resolveModelConfigState: (...args: unknown[]) => resolveModelConfigStateMock(...args) }));

const jobTypes = {
    standard: ["trainer.py", "config.json"],
    diffusion: ["trainer.py", "config.json", "diffusion.py"]
};

vi.mock("@/services/model-service", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/services/model-service")>();

    return {
        ...actual,
        fetchJobTypes: vi.fn().mockResolvedValue(jobTypes),
        getModel: vi.fn(),
        editModel: vi.fn()
    };
});

const stubs = {
    AiBreadcrumbs: { template: "<div />" },
    AiButton: { template: "<button><slot /></button>" },
    AiGuard: { template: "<div><slot /></div>" },
    AiLoader: { template: "<div data-test='loader' />" },
    AiSteps: { template: "<div />" },
    ModelDetails: { template: "<div />" },
    ModelUpload: {
        name: "ModelUpload",
        template: "<div data-test='model-upload' />",
        emits: ["uploaded", "deleted-file"]
    },
    QueryDetails: { template: "<div />" },
    Training: { template: "<div />" },
    EditModelDrawer: { template: "<div />" }
};

function makeModel(files: { name: string; status: string }[]): IModelDashboard {
    return {
        modelId: "test-model",
        projectId: "test-project",
        modelName: "Test Model",
        modelDescription: "",
        status: "PENDING",
        query: {
            name: "",
            query: "",
            results: []
        },
        files: files as IModelDashboard["files"]
    };
}

async function flushPromises(): Promise<void> {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
}

async function mountPage() {
    const ModelPage = (await import("@/pages/project/[projectId]/model/[modelId]/index.vue")).default;
    const pinia = createTestingPinia({
        createSpy: vi.fn,
        stubActions: false
    });
    pinia.state.value.project = {
        project: {
            id: "test-project",
            name: "P",
            status: "APPROVED"
        }
    };
    const wrapper = mount(ModelPage, {
        global: {
            plugins: [pinia],
            stubs,
            directives: { tippy: () => {} }
        }
    });
    await flushPromises();

    return wrapper;
}

beforeEach(() => {
    mockSwrvData.value = undefined;
    mockSwrvError.value = null;
    mutateMock.mockReset();
    resolveModelConfigStateMock.mockReset();
    resolveModelConfigStateMock.mockResolvedValue({
        changed: true,
        configStatus: null,
        jobType: "standard",
        requiredFiles: jobTypes.standard
    });
});

describe("pages/project/[projectId]/model/[modelId]", () => {
    it("renders the loader until model data resolves", async () => {
        const wrapper = await mountPage();
        expect(wrapper.find("[data-test='loader']").exists()).toBe(true);
    });

    it("invokes resolveModelConfigState with the current config.json status on each poll", async () => {
        const wrapper = await mountPage();
        mockSwrvData.value = makeModel([
            {
                name: "config.json",
                status: FileUploadStatus.SCANNING
            }
        ]);
        await flushPromises();
        await wrapper.vm.$nextTick();
        await flushPromises();

        expect(resolveModelConfigStateMock).toHaveBeenCalled();
        const call = resolveModelConfigStateMock.mock.calls.at(-1);
        expect(call?.[0]).toEqual([{
            name: "config.json",
            status: FileUploadStatus.SCANNING
        }]);
        // previousStatus starts as null
        expect(call?.[1]).toBeNull();
        expect(call?.[2]).toEqual(jobTypes);
        expect(call?.[3]).toBe("test-model");
    });

    it("passes the previously resolved status back into the helper on subsequent polls", async () => {
        resolveModelConfigStateMock.mockResolvedValue({
            changed: true,
            configStatus: FileUploadStatus.COMPLETED,
            jobType: "diffusion",
            requiredFiles: jobTypes.diffusion
        });
        mockSwrvData.value = makeModel([
            {
                name: "config.json",
                status: FileUploadStatus.COMPLETED
            }
        ]);
        const wrapper = await mountPage();
        await flushPromises();
        await wrapper.vm.$nextTick();
        await flushPromises();

        // Trigger a second poll with a new object reference but unchanged content
        mockSwrvData.value = makeModel([
            {
                name: "config.json",
                status: FileUploadStatus.COMPLETED
            }
        ]);
        await flushPromises();
        await wrapper.vm.$nextTick();
        await flushPromises();

        // The helper was called at least twice; the last call must see the resolved status
        expect(resolveModelConfigStateMock.mock.calls.length).toBeGreaterThanOrEqual(2);
        const latest = resolveModelConfigStateMock.mock.calls.at(-1);
        expect(latest?.[1]).toBe(FileUploadStatus.COMPLETED);
    });

    it("does not advance the tracked status when the helper reports no change", async () => {
        // Phase 1: helper reports a real transition to SCANNING; tracker advances.
        resolveModelConfigStateMock.mockResolvedValue({
            changed: true,
            configStatus: FileUploadStatus.SCANNING,
            jobType: "standard",
            requiredFiles: jobTypes.standard
        });
        mockSwrvData.value = makeModel([
            {
                name: "config.json",
                status: FileUploadStatus.SCANNING
            }
        ]);
        const wrapper = await mountPage();
        await flushPromises();
        await wrapper.vm.$nextTick();
        await flushPromises();

        // Phase 2: helper returns no-change (e.g. transient fetch failure).
        // Tracker must stay at SCANNING regardless of how many times the watch fires.
        resolveModelConfigStateMock.mockResolvedValue({ changed: false });
        mockSwrvData.value = makeModel([
            {
                name: "config.json",
                status: FileUploadStatus.COMPLETED
            }
        ]);
        await flushPromises();
        await wrapper.vm.$nextTick();
        await flushPromises();

        // Phase 3: clear history and capture the next watch fire's previousStatus.
        // It must still be SCANNING — proving the page did not advance during Phase 2.
        resolveModelConfigStateMock.mockClear();
        resolveModelConfigStateMock.mockResolvedValue({
            changed: true,
            configStatus: FileUploadStatus.COMPLETED,
            jobType: "diffusion",
            requiredFiles: jobTypes.diffusion
        });
        mockSwrvData.value = makeModel([
            {
                name: "config.json",
                status: FileUploadStatus.COMPLETED
            }
        ]);
        await flushPromises();
        await wrapper.vm.$nextTick();
        await flushPromises();

        expect(resolveModelConfigStateMock.mock.calls.length).toBeGreaterThanOrEqual(1);
        const firstPhase3Call = resolveModelConfigStateMock.mock.calls[0];
        expect(firstPhase3Call?.[1]).toBe(FileUploadStatus.SCANNING);
    });

    it("resets the tracked status when ModelUpload emits deleted-file", async () => {
        resolveModelConfigStateMock.mockResolvedValue({
            changed: true,
            configStatus: FileUploadStatus.COMPLETED,
            jobType: "standard",
            requiredFiles: jobTypes.standard
        });
        mockSwrvData.value = makeModel([
            {
                name: "config.json",
                status: FileUploadStatus.COMPLETED
            }
        ]);
        const wrapper = await mountPage();
        await flushPromises();
        await wrapper.vm.$nextTick();
        await flushPromises();

        resolveModelConfigStateMock.mockClear();
        mutateMock.mockClear();

        const upload = wrapper.findComponent({ name: "ModelUpload" });
        expect(upload.exists()).toBe(true);
        await upload.vm.$emit("deleted-file");
        await flushPromises();

        // onFileDeleted triggers mutate() to refresh SWRV data
        expect(mutateMock).toHaveBeenCalled();

        // Simulate the refreshed poll response; helper should see previousStatus=null again
        mockSwrvData.value = makeModel([
            {
                name: "trainer.py",
                status: FileUploadStatus.COMPLETED
            }
        ]);
        await flushPromises();
        await wrapper.vm.$nextTick();
        await flushPromises();

        const call = resolveModelConfigStateMock.mock.calls.at(-1);
        expect(call?.[1]).toBeNull();
    });
});
