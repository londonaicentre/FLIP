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
import { flushPromises, mount, VueWrapper } from "@vue/test-utils";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { FileInfo, FileUploadStatus } from "@/interfaces/model/types";
import ModelUpload from "@/partials/models/ModelUpload.vue";
import { useAuthStore } from "@/store/auth";

// The component uses route.params.modelId inside uploadFile. Reactive
// object shared by both uses so tests can flip the id mid-run.
const mockRoute = {
    params: {
        modelId: "model-under-test",
        projectId: "project-1"
    } as Record<string, string>
};

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => mockRoute
    };
});

// Service stubs — we don't want the tests to reach the real axios client.
const mockCreatePreSignedUrl = vi.fn();
const mockUploadFileService = vi.fn();
// vi.mock factories are hoisted above any top-level code, so the mocked
// FileTooLargeError class has to be defined inside vi.hoisted to be
// available when the mock factory runs.
const { FileTooLargeError } = vi.hoisted(() => {
    class FileTooLargeError extends Error {
        public readonly limitBytes: number;
        public readonly actualBytes: number;
        constructor(limitBytes: number, actualBytes: number) {
            super(`File is ${actualBytes} bytes, which exceeds the ${limitBytes}-byte limit.`);
            this.name = "FileTooLargeError";
            this.limitBytes = limitBytes;
            this.actualBytes = actualBytes;
        }
    }

    return { FileTooLargeError };
});
vi.mock("@/utils/file", () => ({
    createPreSignedUrl: (...args: unknown[]) => mockCreatePreSignedUrl(...args),
    uploadFile: (...args: unknown[]) => mockUploadFileService(...args),
    FileTooLargeError
}));

const policyFor = (overrides: { maxBytes?: number } = {}) => ({
    url: "https://s3.example/upload",
    fields: {
        key: "uploads/model.py",
        "Content-Type": "text/plain"
    },
    maxBytes: overrides.maxBytes ?? 100 * 1024 * 1024
});

const mockProcessScannedFile = vi.fn();
const mockDeleteModelFile = vi.fn();
const mockDownloadModelFile = vi.fn();
const mockGetModelFileDownloadUrl = vi.fn();
vi.mock("@/services/file-service", () => ({
    processScannedFile: (...args: unknown[]) => mockProcessScannedFile(...args),
    deleteModelFile: (...args: unknown[]) => mockDeleteModelFile(...args),
    downloadModelFile: (...args: unknown[]) => mockDownloadModelFile(...args),
    getModelFileDownloadUrl: (...args: unknown[]) => mockGetModelFileDownloadUrl(...args)
}));

// JobType is imported by ModelUpload only as a type annotation; the
// value itself is never read at runtime but the module needs to resolve.
vi.mock("@/services/model-service", () => ({ JobType: {} }));

// Mock JSZip: the real package's generateAsync({ type: "blob" }) needs
// browser File API support that jsdom doesn't fully provide. The mock
// constructor records the files added via file() and returns a fake blob
// from generateAsync so downloadAllAsZip's <a>-click plumbing runs.
const jszipAddedFiles: string[] = [];
vi.mock("jszip", () => {
    return {
        default: class FakeJSZip {
            // Real JSZip signature is `file(name, data)` / `generateAsync(options)` — the
            // mock drops the extra args silently (JS doesn't enforce arity on method
            // calls), so the call sites in ModelUpload.vue work unchanged against the
            // shim. Trimmed here so `@typescript-eslint/no-unused-vars` stays clean
            // without an inline ignore.
            file(name: string) {
                jszipAddedFiles.push(name);
            }
            async generateAsync() {
                return new Blob(["zip-bytes"], { type: "application/zip" });
            }
        }
    };
});

const mockSnackbarSuccess = vi.fn();
const mockSnackbarError = vi.fn();
vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        success: (...args: unknown[]) => mockSnackbarSuccess(...args),
        error: (...args: unknown[]) => mockSnackbarError(...args)
    }
}));

// uploadFile sleeps 3s between the upload and processScannedFile — and
// schedules a 10s setTimeout to emit "uploaded". Use fake timers in the
// upload-flow tests so we can advance past both without actually waiting.
// Left out of non-upload tests so the watch/mount lifecycle runs normally.

// Helper to build a mock File (jsdom implements File natively).
function makeFile(name: string, size = 1024, type = "text/plain"): File {
    return new File(["x".repeat(size)], name, { type });
}

// FileList doesn't have a public constructor; build a mock that satisfies
// the iterable protocol + Array.from() usage the component relies on.
function makeFileList(files: File[]): FileList {
    const list = files as File[] & {
        item?: (i: number) => File | null;
        length?: number;
    };
    list.item = (i: number) => files[i] ?? null;
    Object.defineProperty(list, "length", { value: files.length });

    return list as unknown as FileList;
}

const baseProps = {
    files: [] as FileInfo[],
    loading: false,
    canUpload: true,
    modelId: "model-under-test",
    requiredFiles: [],
    jobType: "standard"
};

function mountModelUpload(
    overrides: Partial<typeof baseProps> = {},
    opts: { hasPermissions?: boolean } = {}
): VueWrapper<unknown> {
    const wrapper = mount(ModelUpload, {
        props: {
            ...baseProps,
            ...overrides
        },
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false
                })
            ],
            // Stub child components and icons so mount doesn't try to
            // resolve styling or render canvas elements.
            stubs: {
                FileUpload: {
                    // Explicit name so findComponent({name:"FileUpload"})
                    // resolves the stub — the default anonymous stub isn't
                    // nameable by reference otherwise.
                    name: "FileUpload",
                    template: "<div data-test=\"file-upload\"><slot /></div>",
                    emits: ["new-files", "newFiles"]
                },
                AiCard: { template: "<div data-test=\"ai-card\"><slot /></div>" },
                AiSkeleton: { template: "<div data-test=\"ai-skeleton\" />" },
                AiLoader: { template: "<div data-test=\"ai-loader\" />" },
                AiButton: {
                    // Mirrors the real AiButton shape (wrapper div + native
                    // button, aria-label as a declared prop) so accessibility
                    // assertions exercise the real wiring, not the stub's.
                    template: "<div @click=\"$emit('click')\"><button :aria-label=\"ariaLabel\" :disabled=\"loading\"><slot /></button></div>",
                    props: ["small", "loading", "ariaLabel"],
                    emits: ["click"]
                },
                AiConfirmModal: {
                    template:
                        "<div data-test=\"confirm-modal\" :data-open=\"dialog\">" +
                        "<button data-test=\"confirm-modal-continue\" @click=\"continueAction()\">Continue</button>" +
                        "<button data-test=\"confirm-modal-close\" @click=\"$emit('close-modal')\">Close</button>" +
                        "<slot />" +
                        "</div>",
                    props: ["dialog", "confirmationText", "continueButtonText", "continueAction", "submitting"],
                    emits: ["close-modal"]
                },
                Transition: { template: "<div><slot /></div>" }
            }
        }
    });
    // Drive `isViewer` via hasPermissions (the component calls
    // authStore.hasPermissions(["CanManageProjects"])). Mock that
    // here so we can toggle viewer vs researcher/admin between tests.
    const authStore = useAuthStore();
    (authStore.hasPermissions as unknown as ReturnType<typeof vi.fn>) = vi.fn(() =>
        opts.hasPermissions ?? true
    );

    return wrapper;
}

describe("ModelUpload", () => {
    beforeEach(() => {
        vi.useRealTimers();
        mockCreatePreSignedUrl.mockReset();
        mockUploadFileService.mockReset();
        mockProcessScannedFile.mockReset();
        mockDeleteModelFile.mockReset();
        mockDownloadModelFile.mockReset();
        mockGetModelFileDownloadUrl.mockReset();
        mockSnackbarSuccess.mockReset();
        mockSnackbarError.mockReset();
        mockRoute.params = {
            modelId: "model-under-test",
            projectId: "project-1"
        };
        // Default: no blacklist. Individual tests re-arm as needed.
        (window as unknown as { BLACKLISTED_MODEL_FILES?: string }).BLACKLISTED_MODEL_FILES = "";
    });

    describe("rendering", () => {
        test("renders the Model Files card header", () => {
            const wrapper = mountModelUpload();
            expect(wrapper.text()).toContain("Model Files");
        });

        test("renders the FileUpload child when canUpload is true and not loading", () => {
            const wrapper = mountModelUpload({
                canUpload: true,
                loading: false
            });
            expect(wrapper.find("[data-test='file-upload']").exists()).toBe(true);
        });

        test("hides the FileUpload child when canUpload is false", () => {
            // Viewers (no CanManageProjects permission) see the listing
            // but not the upload affordance.
            const wrapper = mountModelUpload({ canUpload: false });
            expect(wrapper.find("[data-test='file-upload']").exists()).toBe(false);
        });

        test("shows skeleton placeholders while loading", () => {
            const wrapper = mountModelUpload({ loading: true });
            expect(wrapper.findAll("[data-test='ai-skeleton']").length).toBeGreaterThan(0);
        });
    });

    describe("props.files handling", () => {
        test("gives the icon-only file action buttons accessible names", async () => {
            const wrapper = mountModelUpload({
                files: [{
                    id: "1",
                    name: "model.py",
                    size: 1024,
                    status: FileUploadStatus.COMPLETED
                }],
                loading: false
            });
            await flushPromises();

            // Screen readers announce icon-only buttons as just "button"
            // without an accessible name (Lighthouse button-name).
            expect(wrapper.find("button[aria-label='Download model.py']").exists()).toBe(true);
            expect(wrapper.find("button[aria-label='Delete model.py']").exists()).toBe(true);
        });

        test("hides the delete button on errored files when the user cannot upload", async () => {
            const wrapper = mountModelUpload({
                files: [{
                    id: "1",
                    name: "model.py",
                    size: 1024,
                    status: FileUploadStatus.ERROR
                }],
                canUpload: false,
                loading: false
            });
            await flushPromises();

            // `canUpload && COMPLETED || ERROR` used to expose the destructive
            // action to viewers / after training locked the model.
            expect(wrapper.find("button[aria-label='Delete model.py']").exists()).toBe(false);
        });

        test("shows the delete button on errored files when the user can upload", async () => {
            const wrapper = mountModelUpload({
                files: [{
                    id: "1",
                    name: "model.py",
                    size: 1024,
                    status: FileUploadStatus.ERROR
                }],
                canUpload: true,
                loading: false
            });
            await flushPromises();

            expect(wrapper.find("button[aria-label='Delete model.py']").exists()).toBe(true);
        });

        test("mirrors props.files into the visible list on mount", async () => {
            const files: FileInfo[] = [
                {
                    id: "1",
                    name: "model.py",
                    size: 1024,
                    status: FileUploadStatus.COMPLETED
                },
                {
                    id: "2",
                    name: "config.yaml",
                    size: 512,
                    status: FileUploadStatus.COMPLETED
                }
            ];
            const wrapper = mountModelUpload({
                files,
                loading: false
            });
            // handleFiles populates internalFiles from onMounted; wait for
            // the resulting render before asserting on the DOM text.
            await flushPromises();

            expect(wrapper.text()).toContain("model.py");
            expect(wrapper.text()).toContain("config.yaml");
        });

        test("updates the list when props.files change after mount", async () => {
            const wrapper = mountModelUpload({
                files: [],
                loading: false
            });
            expect(wrapper.text()).not.toContain("model.py");

            await wrapper.setProps({
                files: [
                    {
                        id: "1",
                        name: "model.py",
                        size: 1024,
                        status: FileUploadStatus.COMPLETED
                    }
                ],
                loading: false
            });

            expect(wrapper.text()).toContain("model.py");
        });

        test("clears uploadingFiles entries when matching files arrive from the server", async () => {
            // After a successful upload, filesAreUploading flips true and
            // the list shows the UPLOADING pill. When the parent refetches
            // and passes the same file back as a real FileInfo from /files,
            // handleFiles must remove it from uploadingFiles so we don't
            // show two rows for the same filename (one SCANNING, one COMPLETED).
            mockCreatePreSignedUrl.mockResolvedValue("https://s3.example/signed-url");
            mockUploadFileService.mockResolvedValue(undefined);
            mockProcessScannedFile.mockResolvedValue(undefined);

            vi.useFakeTimers();
            const wrapper = mountModelUpload();
            wrapper.findComponent({ name: "FileUpload" }).vm.$emit(
                "new-files",
                makeFileList([makeFile("model.py")])
            );
            // Advance past the 3s scan-wait — by this point filesAreUploading
            // should be true and uploadingFiles contains model.py (SCANNING).
            await vi.advanceTimersByTimeAsync(3_500);
            await flushPromises();

            // Parent now passes the completed file back via props.files —
            // this is the real-world "server confirmed the file" update.
            vi.useRealTimers();
            await wrapper.setProps({
                files: [
                    {
                        id: "99",
                        name: "model.py",
                        size: 1024,
                        status: FileUploadStatus.COMPLETED
                    }
                ]
            });
            await flushPromises();

            // One row, not two: the uploadingFiles entry for model.py
            // must have been filtered out by handleFiles's sync branch.
            const rows = wrapper.findAll("li");
            expect(rows.length).toBe(1);
        });
    });

    describe("uploadFile — blacklist", () => {
        test("rejects files whose name is in window.BLACKLISTED_MODEL_FILES", async () => {
            // BLACKLISTED_MODEL_FILES is a comma-separated list populated by
            // scripts/generate-window-js.sh at deploy/dev-start time. Files
            // matching a reserved name must NOT hit the presigned-URL path.
            (window as unknown as { BLACKLISTED_MODEL_FILES: string }).BLACKLISTED_MODEL_FILES =
                "flip.py, server_app.py";
            const wrapper = mountModelUpload();
            const file = makeFile("flip.py");

            wrapper.findComponent({ name: "FileUpload" }).vm.$emit("new-files", makeFileList([file]));
            await flushPromises();

            expect(mockSnackbarError).toHaveBeenCalledWith(
                expect.objectContaining({
                    title: "Error",
                    text: expect.stringContaining("not supported")
                }),
                12_000
            );
            // Crucially: upload-path helpers must not have been called for
            // the blacklisted file.
            expect(mockCreatePreSignedUrl).not.toHaveBeenCalled();
            expect(mockUploadFileService).not.toHaveBeenCalled();
        });

        test("trims whitespace around blacklist entries", async () => {
            // The generator emits values verbatim, so a stray space after
            // a comma must not mask a blacklist hit. Assert the trim.
            (window as unknown as { BLACKLISTED_MODEL_FILES: string }).BLACKLISTED_MODEL_FILES =
                "first.py ,  second.py  ,third.py";
            const wrapper = mountModelUpload();

            wrapper.findComponent({ name: "FileUpload" }).vm.$emit(
                "new-files",
                makeFileList([makeFile("second.py")])
            );
            await flushPromises();

            expect(mockSnackbarError).toHaveBeenCalled();
            expect(mockCreatePreSignedUrl).not.toHaveBeenCalled();
        });

        test("empty BLACKLISTED_MODEL_FILES env var allows all names through", async () => {
            (window as unknown as { BLACKLISTED_MODEL_FILES: string }).BLACKLISTED_MODEL_FILES = "";
            mockCreatePreSignedUrl.mockResolvedValue(policyFor());
            mockUploadFileService.mockResolvedValue(undefined);
            mockProcessScannedFile.mockResolvedValue(undefined);

            vi.useFakeTimers();
            const wrapper = mountModelUpload();
            wrapper.findComponent({ name: "FileUpload" }).vm.$emit(
                "new-files",
                makeFileList([makeFile("custom_model.py")])
            );

            // Advance past the 3s scan-wait inside uploadFile.
            await vi.advanceTimersByTimeAsync(3_500);
            await flushPromises();

            expect(mockCreatePreSignedUrl).toHaveBeenCalled();
            expect(mockSnackbarError).not.toHaveBeenCalled();
        });
    });

    describe("uploadFile — happy path", () => {
        test("obtains a presigned policy, uploads, marks SCANNING, then processes the file", async () => {
            const policy = policyFor();
            mockCreatePreSignedUrl.mockResolvedValue(policy);
            mockUploadFileService.mockResolvedValue(undefined);
            mockProcessScannedFile.mockResolvedValue(undefined);

            vi.useFakeTimers();
            const wrapper = mountModelUpload();
            wrapper.findComponent({ name: "FileUpload" }).vm.$emit(
                "new-files",
                makeFileList([makeFile("model.py")])
            );

            // Drain microtasks so createPreSignedUrl + uploadFileService resolve.
            await vi.advanceTimersByTimeAsync(0);
            await flushPromises();

            expect(mockCreatePreSignedUrl).toHaveBeenCalledWith(
                expect.objectContaining({ name: "model.py" }),
                "/files/preSignedUrl/model",
                "model-under-test"
            );
            expect(mockUploadFileService).toHaveBeenCalledWith(
                expect.objectContaining({ name: "model.py" }),
                policy
            );
            expect(mockSnackbarSuccess).toHaveBeenCalledWith(
                expect.objectContaining({ title: "File Uploaded!" })
            );

            // Advance past the 3s scan-wait so processScannedFile runs.
            await vi.advanceTimersByTimeAsync(3_500);
            await flushPromises();

            expect(mockProcessScannedFile).toHaveBeenCalledWith(
                "/files/process-scanned-file/model-under-test/model.py"
            );
        });

        test("emits 'uploaded' 10s after the upload batch completes", async () => {
            mockCreatePreSignedUrl.mockResolvedValue(policyFor());
            mockUploadFileService.mockResolvedValue(undefined);
            mockProcessScannedFile.mockResolvedValue(undefined);

            vi.useFakeTimers();
            const wrapper = mountModelUpload();
            wrapper.findComponent({ name: "FileUpload" }).vm.$emit(
                "new-files",
                makeFileList([makeFile("model.py")])
            );

            await vi.advanceTimersByTimeAsync(3_500);
            await flushPromises();
            // Not yet — the emit is behind a 10s setTimeout.
            expect(wrapper.emitted("uploaded")).toBeFalsy();

            await vi.advanceTimersByTimeAsync(10_500);
            await flushPromises();
            expect(wrapper.emitted("uploaded")).toEqual([[true]]);
        });
    });

    describe("uploadFile — error paths", () => {
        test("marks the file ERROR and snackbars when createPreSignedUrl returns null", async () => {
            // The component treats a null/empty presigned policy as an error —
            // the upload cannot proceed without somewhere to POST the bytes.
            mockCreatePreSignedUrl.mockResolvedValue(null);

            vi.useFakeTimers();
            const wrapper = mountModelUpload();
            wrapper.findComponent({ name: "FileUpload" }).vm.$emit(
                "new-files",
                makeFileList([makeFile("model.py")])
            );

            await vi.advanceTimersByTimeAsync(0);
            await flushPromises();

            expect(mockUploadFileService).not.toHaveBeenCalled();
            expect(mockSnackbarError).toHaveBeenCalledWith(
                expect.objectContaining({ title: "Error uploading file" })
            );
        });

        test("marks the file ERROR when the S3 POST throws", async () => {
            mockCreatePreSignedUrl.mockResolvedValue(policyFor());
            mockUploadFileService.mockRejectedValue(new Error("network blip"));

            vi.useFakeTimers();
            const wrapper = mountModelUpload();
            wrapper.findComponent({ name: "FileUpload" }).vm.$emit(
                "new-files",
                makeFileList([makeFile("model.py")])
            );

            await vi.advanceTimersByTimeAsync(0);
            await flushPromises();

            expect(mockSnackbarError).toHaveBeenCalledWith(
                expect.objectContaining({ title: "Error uploading file" })
            );
            // processScannedFile must NOT run when the upload itself failed.
            expect(mockProcessScannedFile).not.toHaveBeenCalled();
        });

        test("rejects oversized files locally with a clear snackbar before any upload", async () => {
            // Client-side guard: if the file is larger than the policy's
            // maxBytes, we must not even start the POST. This exists so a
            // legitimate user gets a clear error rather than letting S3
            // reject the upload after bytes have already been streamed.
            mockCreatePreSignedUrl.mockResolvedValue(policyFor({ maxBytes: 8 }));

            vi.useFakeTimers();
            const wrapper = mountModelUpload();
            wrapper.findComponent({ name: "FileUpload" }).vm.$emit(
                "new-files",
                makeFileList([makeFile("model.py", 1024)])
            );

            await vi.advanceTimersByTimeAsync(0);
            await flushPromises();

            expect(mockUploadFileService).not.toHaveBeenCalled();
            expect(mockSnackbarError).toHaveBeenCalledWith(
                expect.objectContaining({ title: "File too large" }),
                12_000
            );
            expect(mockProcessScannedFile).not.toHaveBeenCalled();
        });
    });

    describe("delete flow", () => {
        test("deleteFile calls the backend with the chosen filename and emits deletedFile", async () => {
            mockDeleteModelFile.mockResolvedValue(undefined);
            const files: FileInfo[] = [
                {
                    id: "1",
                    name: "model.py",
                    size: 1024,
                    status: FileUploadStatus.COMPLETED
                }
            ];
            const wrapper = mountModelUpload({ files });
            await flushPromises();

            // Two buttons render in the row when canUpload && status=COMPLETED:
            // [0] download (inside the Transition stub), [1] delete (inside
            // the Transition stub). Click the delete button — its handler
            // calls confirmDeleteFile(name), which sets fileToDelete and
            // opens the modal.
            const rowButtons = wrapper.findAll("li button");
            expect(rowButtons.length).toBe(2);
            await rowButtons[1].trigger("click");
            await flushPromises();

            // The modal stub's `continueAction` prop is the component's
            // deleteFile handler; the continue button stub calls it.
            await wrapper.find("[data-test='confirm-modal-continue']").trigger("click");
            await flushPromises();

            expect(mockDeleteModelFile).toHaveBeenCalledWith(
                "/files/model/model-under-test/model.py"
            );
            expect(wrapper.emitted("deletedFile")).toBeTruthy();
        });
    });

    describe("download flow", () => {
        test("downloadFile navigates an <a> straight to the presigned URL (no Blob)", async () => {
            // The per-file button must NOT buffer the file into memory: it
            // fetches only the presigned URL and lets the browser's download
            // manager stream from S3 (Content-Disposition: attachment is set
            // server-side). A Blob round-trip would cap downloads at what the
            // tab can hold — files can reach MAX_MODEL_FILE_BYTES (5 GiB).
            mockGetModelFileDownloadUrl.mockResolvedValue({
                url: "https://s3.example.com/signed-download",
                fileName: "model.py"
            });

            let clickedHref: string | undefined;
            const clickSpy = vi
                .spyOn(HTMLAnchorElement.prototype, "click")
                .mockImplementation(function (this: HTMLAnchorElement) {
                    clickedHref = this.href;
                });

            const files: FileInfo[] = [
                {
                    id: "1",
                    name: "model.py",
                    size: 1024,
                    status: FileUploadStatus.COMPLETED
                }
            ];
            const wrapper = mountModelUpload({ files }, { hasPermissions: true });
            await flushPromises();

            // hasPermissions=true → isViewer=false → download button
            // visible. Two row buttons: [0] download (first Transition),
            // [1] delete (second Transition).
            const rowButtons = wrapper.findAll("li button");
            expect(rowButtons.length).toBe(2);
            await rowButtons[0].trigger("click");
            await flushPromises();

            expect(mockGetModelFileDownloadUrl).toHaveBeenCalledWith(
                "/files/model/model-under-test/model.py"
            );
            expect(clickedHref).toBe("https://s3.example.com/signed-download");
            // The byte-fetching path must stay untouched by a plain download.
            expect(mockDownloadModelFile).not.toHaveBeenCalled();

            clickSpy.mockRestore();
        });

        test("downloadFile snackbars with the file name when the URL request rejects", async () => {
            // Previously downloadFile had no catch at all — a rejection failed
            // silently with no user-visible feedback.
            mockGetModelFileDownloadUrl.mockRejectedValueOnce(new Error("network"));

            const files: FileInfo[] = [
                {
                    id: "1",
                    name: "model.py",
                    size: 1024,
                    status: FileUploadStatus.COMPLETED
                }
            ];
            const wrapper = mountModelUpload({ files }, { hasPermissions: true });
            await flushPromises();

            const rowButtons = wrapper.findAll("li button");
            await rowButtons[0].trigger("click");
            await flushPromises();

            expect(mockSnackbarError).toHaveBeenCalledWith({
                title: "Download failed",
                text: "Could not download model.py. Please try again."
            });
        });

        test("viewer (no CanManageProjects) does not see the download button", async () => {
            const files: FileInfo[] = [
                {
                    id: "1",
                    name: "model.py",
                    size: 1024,
                    status: FileUploadStatus.COMPLETED
                }
            ];
            // Viewers can view the file list but can't download. canUpload
            // is false for viewers so delete is also hidden.
            const wrapper = mountModelUpload(
                {
                    files,
                    canUpload: false
                },
                { hasPermissions: false }
            );
            await flushPromises();

            // No row buttons should render at all for a viewer.
            expect(wrapper.findAll("li button").length).toBe(0);
        });

        test("download-all-files-btn fetches each file, zips them and triggers an <a> download", async () => {
            const blob1 = new Blob(["a"], { type: "text/plain" });
            const blob2 = new Blob(["b"], { type: "text/plain" });
            mockDownloadModelFile.mockImplementation((path: string) =>
                Promise.resolve(path.endsWith("a.py") ? blob1 : blob2)
            );
            const createObjectURLSpy = vi
                .spyOn(URL, "createObjectURL").mockReturnValue("blob:zip-fake");
            const revokeObjectURLSpy = vi
                .spyOn(URL, "revokeObjectURL").mockReturnValue(undefined);
            const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockReturnValue();

            const files: FileInfo[] = [
                {
                    id: "1",
                    name: "a.py",
                    size: 10,
                    status: FileUploadStatus.COMPLETED
                },
                {
                    id: "2",
                    name: "b.py",
                    size: 12,
                    status: FileUploadStatus.COMPLETED
                }
            ];
            const wrapper = mountModelUpload({ files }, { hasPermissions: true });
            await flushPromises();

            await wrapper.find("[data-test=download-all-files-btn]").trigger("click");
            await flushPromises();

            // One downloadModelFile call per file, in parallel.
            expect(mockDownloadModelFile).toHaveBeenCalledWith("/files/model/model-under-test/a.py");
            expect(mockDownloadModelFile).toHaveBeenCalledWith("/files/model/model-under-test/b.py");
            // The zip blob got an object URL and the <a> click was triggered.
            expect(createObjectURLSpy).toHaveBeenCalled();
            expect(clickSpy).toHaveBeenCalled();
            expect(revokeObjectURLSpy).toHaveBeenCalledWith("blob:zip-fake");

            createObjectURLSpy.mockRestore();
            revokeObjectURLSpy.mockRestore();
            clickSpy.mockRestore();
        });

        test("download-all collapses its label below lg and keeps an aria-label", async () => {
            // Same collapse treatment as the page-header actions: below lg the
            // label hides leaving the icon, with an aria-label on the native
            // button keeping it named for screen readers.
            const files: FileInfo[] = [
                {
                    id: "1",
                    name: "a.py",
                    size: 10,
                    status: FileUploadStatus.COMPLETED
                }
            ];
            const wrapper = mountModelUpload({ files }, { hasPermissions: true });
            await flushPromises();

            const holder = wrapper.find("[data-test=download-all-files-btn]");
            expect(holder.exists()).toBe(true);
            // aria-label is a declared AiButton prop wired to the inner native button.
            expect(holder.find("button").attributes("aria-label")).toBe("Download all");
            const label = holder.find("span.hidden.lg\\:inline");
            expect(label.exists()).toBe(true);
            expect(label.text()).toBe("Download all");
        });

        test("download-all snackbars when a file fetch fails", async () => {
            mockDownloadModelFile.mockRejectedValue(new Error("network"));
            const createObjectURLSpy = vi
                .spyOn(URL, "createObjectURL").mockReturnValue("blob:nope");
            const revokeObjectURLSpy = vi
                .spyOn(URL, "revokeObjectURL").mockReturnValue(undefined);

            const files: FileInfo[] = [
                {
                    id: "1",
                    name: "a.py",
                    size: 10,
                    status: FileUploadStatus.COMPLETED
                }
            ];
            const wrapper = mountModelUpload({ files }, { hasPermissions: true });
            await flushPromises();

            await wrapper.find("[data-test=download-all-files-btn]").trigger("click");
            await flushPromises();

            expect(mockSnackbarError).toHaveBeenCalledWith(
                expect.objectContaining({ title: "Download failed" })
            );

            createObjectURLSpy.mockRestore();
            revokeObjectURLSpy.mockRestore();
        });

        test("download-all is a no-op while a previous download is still in flight", async () => {
            // Hold the first downloadModelFile open so we can fire the button
            // again before the first batch finishes.
            let resolveFirst: ((b: Blob) => void) | null = null;
            mockDownloadModelFile.mockReturnValueOnce(
                new Promise<Blob>(resolve => { resolveFirst = resolve; })
            );

            const files: FileInfo[] = [
                {
                    id: "1",
                    name: "a.py",
                    size: 10,
                    status: FileUploadStatus.COMPLETED
                }
            ];
            const wrapper = mountModelUpload({ files }, { hasPermissions: true });
            await flushPromises();

            await wrapper.find("[data-test=download-all-files-btn]").trigger("click");
            // Second click should bail at the `if (downloadingAll.value) return` guard.
            await wrapper.find("[data-test=download-all-files-btn]").trigger("click");
            expect(mockDownloadModelFile).toHaveBeenCalledTimes(1);

            // Wrap up so we don't leak a pending promise.
            resolveFirst!(new Blob(["x"]));
        });

        test("download-all fetches in batches of 3, not all files at once", async () => {
            // 4 files -> batch 1 is the first 3, batch 2 is the remaining 1.
            // Hold every download open so we can prove batch 2 hasn't started
            // until batch 1 fully resolves.
            const resolvers: Array<(b: Blob) => void> = [];
            mockDownloadModelFile.mockImplementation(() =>
                new Promise<Blob>(resolve => { resolvers.push(resolve); })
            );

            const files: FileInfo[] = Array.from({ length: 4 }, (_, i) => ({
                id: String(i + 1),
                name: `file-${i + 1}.py`,
                size: 10,
                status: FileUploadStatus.COMPLETED
            }));
            const wrapper = mountModelUpload({ files }, { hasPermissions: true });
            await flushPromises();

            await wrapper.find("[data-test=download-all-files-btn]").trigger("click");
            await flushPromises();

            // Only the first batch (3 files) should have started.
            expect(mockDownloadModelFile).toHaveBeenCalledTimes(3);

            // Resolving batch 1 lets the loop move on to batch 2 (1 file).
            resolvers.splice(0).forEach(resolve => resolve(new Blob(["x"])));
            await flushPromises();
            expect(mockDownloadModelFile).toHaveBeenCalledTimes(4);

            // Resolve the last file so the zip generation completes cleanly.
            resolvers.splice(0).forEach(resolve => resolve(new Blob(["y"])));
            await flushPromises();
        });
    });
});
