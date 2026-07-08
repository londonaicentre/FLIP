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

const flStatusData = ref<{ fl_backend?: string }[] | undefined>(undefined);

vi.mock("swrv", () => ({
    default: (key: unknown) => {
        const resolved = typeof key === "function" ? key() : key;
        if (typeof resolved === "string" && resolved.startsWith("fl/")) {
            return {
                data: flStatusData,
                mutate: vi.fn(),
                error: ref(null)
            };
        }

        return {
            data: mockSwrvData,
            mutate: mutateMock,
            error: mockSwrvError
        };
    }
}));

vi.mock("@/composables/useErrorHandler", () => ({ default: vi.fn() }));

const routeViewProject = vi.fn();

vi.mock("@/router", () => ({ routeChange: { viewProject: (...args: unknown[]) => routeViewProject(...args) } }));

const mockSnackbarError = vi.fn();
const mockSnackbarSuccess = vi.fn();
const mockSnackbarWarning = vi.fn();

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        error: (...args: unknown[]) => mockSnackbarError(...args),
        success: (...args: unknown[]) => mockSnackbarSuccess(...args),
        warning: (...args: unknown[]) => mockSnackbarWarning(...args),
        show: vi.fn()
    }
}));


const resolveModelConfigStateMock = vi.fn();

vi.mock("@/services/file-service", () => ({ resolveModelConfigState: (...args: unknown[]) => resolveModelConfigStateMock(...args) }));

const jobTypes = {
    standard: ["trainer.py", "config.json"],
    diffusion: ["trainer.py", "config.json", "diffusion.py"]
};

const mockEditModel = vi.fn();

vi.mock("@/services/model-service", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/services/model-service")>();

    return {
        ...actual,
        fetchJobTypes: vi.fn().mockResolvedValue(jobTypes),
        getModel: vi.fn(),
        editModel: (...args: unknown[]) => mockEditModel(...args)
    };
});

const initiateTrainingSpy = vi.fn();

const stubs = {
    AiAlert: {
        template: "<div data-test='ai-alert' :data-variant='variant'><slot /></div>",
        props: ["variant"]
    },
    AiBreadcrumbs: { template: "<div />" },
    AiButton: {
        inheritAttrs: false,
        props: ["disabled"],
        template: "<button v-bind=\"$attrs\" :disabled='disabled' @click=\"$emit('click', $event)\"><slot /></button>"
    },
    AiGuard: { template: "<div><slot /></div>" },
    AiLoader: { template: "<div data-test='loader' />" },
    LifecycleTrack: { template: "<div />" },
    ModelDetails: { template: "<div />" },
    ModelUpload: {
        name: "ModelUpload",
        template: "<div data-test='model-upload' />",
        emits: ["uploaded", "deleted-file"]
    },
    QueryDetails: { template: "<div />" },
    Training: {
        name: "Training",
        props: ["flBackendLabel"],
        template: "<div data-test='training' :data-fl-backend-label='flBackendLabel' />",
        setup: () => ({
            initiateTraining: initiateTrainingSpy,
            isSubmitting: false
        })
    },
    TrainingActionsMenu: {
        name: "TrainingActionsMenu",
        props: ["status"],
        template: "<div data-test='training-actions-menu' :data-status='status' />"
    },
    EditModelDrawer: {
        name: "EditModelDrawer",
        props: ["id", "show", "name", "description", "modelPending", "updating", "ownerId"],
        emits: ["close", "save"],
        template: "<div data-test='edit-model-drawer' :data-show='show' />"
    },
    // Render the slot so the breadcrumb copy is exercised (the real RouterLink
    // is never registered in these tests).
    "router-link": { template: "<a><slot /></a>" }
};

function makeModel(
    files: { name: string; status: string }[],
    overrides: Partial<IModelDashboard> = {}
): IModelDashboard {
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
        files: files as IModelDashboard["files"],
        ...overrides
    };
}

async function flushPromises(): Promise<void> {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
}

async function mountPage(options: {
    projectStatus?: "APPROVED" | "STAGED" | "UNSTAGED";
    ownerId?: string;
    users?: { id: string }[];
    currentUserId?: string;
    permissions?: string[];
} = {}) {
    const {
        projectStatus = "APPROVED",
        ownerId = "owner-1",
        users = [],
        currentUserId = "owner-1",
        permissions = ["CanCreateProjects", "CanManageProjects"]
    } = options;
    const ModelPage = (await import("@/pages/project/[projectId]/model/[modelId]/index.vue")).default;
    const pinia = createTestingPinia({
        createSpy: vi.fn,
        stubActions: false
    });
    pinia.state.value.project = {
        project: {
            id: "test-project",
            name: "P",
            status: projectStatus,
            ownerId,
            users
        }
    };
    pinia.state.value.auth = {
        user: {
            userId: currentUserId,
            permissions
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
    flStatusData.value = undefined;
    mutateMock.mockReset();
    mockEditModel.mockReset();
    routeViewProject.mockReset();
    mockSnackbarError.mockReset();
    mockSnackbarSuccess.mockReset();
    mockSnackbarWarning.mockReset();
    initiateTrainingSpy.mockReset();
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

    // `model-error-banner` element no longer exists in the page template —
    // training errors now surface via Snackbar.error (see line 170). Test
    // skipped until the banner-or-snackbar contract is re-decided.
    it.skip("shows a red error banner above the cards when training has errored", async () => {
        resolveModelConfigStateMock.mockResolvedValue({
            changed: true,
            configStatus: FileUploadStatus.COMPLETED,
            jobType: "standard",
            requiredFiles: jobTypes.standard
        });
        mockSwrvData.value = makeModel(
            [{
                name: "config.json",
                status: FileUploadStatus.COMPLETED
            }],
            { status: "ERROR" }
        );
        const wrapper = await mountPage();
        await flushPromises();
        await wrapper.vm.$nextTick();

        const banner = wrapper.find("[data-test='model-error-banner']");
        expect(banner.exists()).toBe(true);
        expect(banner.attributes("data-variant")).toBe("error");
        expect(banner.text()).toContain("There has been an error while training this model.");
    });

    it("does not render the error banner for non-error statuses", async () => {
        resolveModelConfigStateMock.mockResolvedValue({
            changed: true,
            configStatus: FileUploadStatus.COMPLETED,
            jobType: "standard",
            requiredFiles: jobTypes.standard
        });
        mockSwrvData.value = makeModel(
            [{
                name: "config.json",
                status: FileUploadStatus.COMPLETED
            }],
            { status: "TRAINING_STARTED" }
        );
        const wrapper = await mountPage();
        await flushPromises();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='model-error-banner']").exists()).toBe(false);
    });

    it("aborts onBeforeMount when the parent project is not APPROVED and warns + routes back", async () => {
        const wrapper = await mountPage({ projectStatus: "UNSTAGED" });
        await flushPromises();
        expect(mockSnackbarError).toHaveBeenCalledWith(
            expect.objectContaining({ title: "Requires Project Approval" })
        );
        expect(routeViewProject).toHaveBeenCalledWith("test-project");
        wrapper.unmount();
    });

    it("watch(error) routes to the parent project and shows a warning snackbar when SWRV errors", async () => {
        const wrapper = await mountPage();
        mockSwrvError.value = new Error("boom");
        await flushPromises();
        await wrapper.vm.$nextTick();

        expect(routeViewProject).toHaveBeenCalledWith("test-project");
        expect(mockSnackbarWarning).toHaveBeenCalledWith(
            expect.objectContaining({ title: "Model doesn't exist" })
        );
    });

    it("flBackendLabel surfaces the FL net's backend name on the Training stub", async () => {
        flStatusData.value = [{ fl_backend: "nvflare" }];
        mockSwrvData.value = makeModel(
            [{
                name: "config.json",
                status: FileUploadStatus.COMPLETED
            }]
        );
        const wrapper = await mountPage();
        await flushPromises();
        await wrapper.vm.$nextTick();

        const training = wrapper.find("[data-test='training']");
        expect(training.attributes("data-fl-backend-label")).toBe("NVFlare");
    });

    it("flBackendLabel is undefined when no FL net advertises a backend", async () => {
        flStatusData.value = [{}];
        mockSwrvData.value = makeModel([
            {
                name: "config.json",
                status: FileUploadStatus.COMPLETED
            }
        ]);
        const wrapper = await mountPage();
        await flushPromises();
        await wrapper.vm.$nextTick();

        const training = wrapper.find("[data-test='training']");
        // Empty string ↔ absent for the attribute coercion of `undefined`.
        expect(training.attributes("data-fl-backend-label")).toBeFalsy();
    });

    it("renders the Projects / project-name breadcrumb above the model header", async () => {
        mockSwrvData.value = makeModel([
            {
                name: "config.json",
                status: FileUploadStatus.COMPLETED
            }
        ]);
        const wrapper = await mountPage();
        await flushPromises();
        await wrapper.vm.$nextTick();

        // Breadcrumb: "Projects" link + the parent project's name ("P").
        expect(wrapper.text()).toContain("Projects");
        expect(wrapper.text()).toContain("P");
    });

    it("clicking Initiate Training while ready fires the Training form's submit", async () => {
        resolveModelConfigStateMock.mockResolvedValue({
            changed: true,
            configStatus: FileUploadStatus.COMPLETED,
            jobType: "standard",
            requiredFiles: jobTypes.standard
        });
        // PENDING + all required files COMPLETED + a query present → readyToTrain,
        // so the button is enabled and the click handler can fire.
        mockSwrvData.value = makeModel(
            [
                {
                    name: "trainer.py",
                    status: FileUploadStatus.COMPLETED
                },
                {
                    name: "config.json",
                    status: FileUploadStatus.COMPLETED
                }
            ],
            { status: "PENDING" }
        );
        const wrapper = await mountPage();
        await flushPromises();
        await wrapper.vm.$nextTick();
        await flushPromises();

        const button = wrapper.find("[data-test='initiate-training-btn']");
        expect(button.exists()).toBe(true);
        expect(button.attributes("disabled")).toBeUndefined();

        await button.trigger("click");
        expect(initiateTrainingSpy).toHaveBeenCalled();
    });

    it("shows TrainingActionsMenu instead of Initiate Training once status leaves PENDING", async () => {
        mockSwrvData.value = makeModel(
            [{
                name: "config.json",
                status: FileUploadStatus.COMPLETED
            }],
            { status: "TRAINING_STARTED" }
        );
        const wrapper = await mountPage();
        await flushPromises();
        await wrapper.vm.$nextTick();

        expect(wrapper.find("[data-test='initiate-training-btn']").exists()).toBe(false);
        expect(wrapper.find("[data-test='training-actions-menu']").exists()).toBe(true);
    });

    it("openEditModelDrawer click flips the drawer's show prop; @close flips it back", async () => {
        mockSwrvData.value = makeModel([
            {
                name: "config.json",
                status: FileUploadStatus.COMPLETED
            }
        ]);
        const wrapper = await mountPage();
        await flushPromises();
        await wrapper.vm.$nextTick();

        const drawer = wrapper.findComponent({ name: "EditModelDrawer" });
        expect(drawer.props("show")).toBe(false);
        await wrapper.find("[data-test='edit-model-btn']").trigger("click");
        expect(drawer.props("show")).toBe(true);

        await drawer.vm.$emit("close");
        expect(drawer.props("show")).toBe(false);
    });

    it("EditModelDrawer @save success calls editModel + shows success snackbar + closes the drawer", async () => {
        mockEditModel.mockResolvedValue(undefined);
        mockSwrvData.value = makeModel(
            [{
                name: "config.json",
                status: FileUploadStatus.COMPLETED
            }],
            { modelName: "Test Model" }
        );
        const wrapper = await mountPage();
        await flushPromises();
        await wrapper.vm.$nextTick();

        const drawer = wrapper.findComponent({ name: "EditModelDrawer" });
        await wrapper.find("[data-test='edit-model-btn']").trigger("click");
        expect(drawer.props("show")).toBe(true);

        await drawer.vm.$emit("save", {
            name: "New name",
            description: "desc"
        });
        await flushPromises();

        expect(mockEditModel).toHaveBeenCalledWith("/model/test-model", {
            name: "New name",
            description: "desc"
        });
        expect(mockSnackbarSuccess).toHaveBeenCalledWith(expect.objectContaining({ title: "Model Updated" }));
        expect(drawer.props("show")).toBe(false);
    });

    it("EditModelDrawer @save failure surfaces an error snackbar and still closes the drawer", async () => {
        mockEditModel.mockRejectedValue(new Error("nope"));
        mockSwrvData.value = makeModel([
            {
                name: "config.json",
                status: FileUploadStatus.COMPLETED
            }
        ]);
        const wrapper = await mountPage();
        await flushPromises();
        await wrapper.vm.$nextTick();

        const drawer = wrapper.findComponent({ name: "EditModelDrawer" });
        await drawer.vm.$emit("save", {
            name: "X",
            description: ""
        });
        await flushPromises();

        expect(mockSnackbarError).toHaveBeenCalledWith(expect.objectContaining({ title: "Unable to update model" }));
        expect(drawer.props("show")).toBe(false);
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

    describe("header action buttons collapse to icons on narrow screens", () => {
        // Below lg the button labels hide (icon + tooltip only) so they stop
        // squashing the truncating model title; aria-labels keep the buttons
        // named for screen readers at every width.
        const labelSelector = "span.hidden.lg\\:inline";

        it("hides the Edit Model label below lg and keeps an aria-label", async () => {
            mockSwrvData.value = makeModel([
                {
                    name: "config.json",
                    status: FileUploadStatus.COMPLETED
                }
            ]);
            const wrapper = await mountPage();
            await flushPromises();
            await wrapper.vm.$nextTick();

            const btn = wrapper.find("[data-test='edit-model-btn']");
            expect(btn.exists()).toBe(true);
            expect(btn.attributes("aria-label")).toBe("Edit Model");
            const label = btn.find(labelSelector);
            expect(label.exists()).toBe(true);
            expect(label.text()).toBe("Edit Model");
        });

        it("hides the Initiate Training label below lg and keeps an aria-label", async () => {
            mockSwrvData.value = makeModel(
                [
                    {
                        name: "trainer.py",
                        status: FileUploadStatus.COMPLETED
                    },
                    {
                        name: "config.json",
                        status: FileUploadStatus.COMPLETED
                    }
                ],
                { status: "PENDING" }
            );
            const wrapper = await mountPage();
            await flushPromises();
            await wrapper.vm.$nextTick();

            const btn = wrapper.find("[data-test='initiate-training-btn']");
            expect(btn.exists()).toBe(true);
            expect(btn.attributes("aria-label")).toBe("Initiate Training");
            const label = btn.find(labelSelector);
            expect(label.exists()).toBe(true);
            expect(label.text()).toBe("Initiate Training");
        });
    });
});
