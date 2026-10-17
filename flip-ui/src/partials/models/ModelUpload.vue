<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

<template>
    <!-- The card fills the height its column is given; only the file list and the
         uploader below it scroll, so the heading and the required-files summary stay put. -->
    <section class="flex flex-col flex-1 min-h-0">
        <AiCard class="flex flex-col flex-1 min-h-0">
            <div class="flex flex-col flex-1 min-h-0">
                <div class="flex items-center gap-3 p-4 shrink-0">
                    <h2 class="text-lg font-semibold leading-loose font-heading grow">
                        Model Files
                    </h2>
                    <AiButton
                        v-if="canDownloadAll"
                        light
                        data-test="download-all-files-btn"
                        aria-label="Download all"
                        tooltip="Download all"
                        :loading="downloadingAll"
                        @click="downloadAllAsZip"
                    >
                        <icon-ph-download-duotone class="w-4 h-4 lg:mr-2" />
                        <span class="hidden lg:inline">Download all</span>
                    </AiButton>
                </div>
                <div class="flex flex-col flex-1 min-h-0 border-t border-gray-200 dark:border-dark-border">
                    <!-- Hidden while the job types are unknown: `jobType` is still the "standard"
                         default then and `requiredFiles` is empty, so stating either would be a
                         guess. Training.vue carries the explanation and the retry. -->
                    <div v-if="showRequired" class="px-4 pt-3 pb-2 space-y-2.5 shrink-0" data-test="required-files-header">
                        <div class="flex items-baseline justify-between gap-2">
                            <span class="text-xs font-semibold tracking-[0.08em] text-gray-500 dark:text-gray-300">
                                FILES · REQUIRED
                            </span>
                            <span
                                data-test="required-files-counter"
                                class="font-mono text-xs"
                                :class="requiredComplete ? 'text-green-600 dark:text-green-400' : 'text-amber-700 dark:text-amber-400'"
                            >
                                {{ requiredUploadedCount }} / {{ requiredFiles.length }}
                            </span>
                        </div>
                        <div class="flex flex-wrap items-center gap-1.5 text-[13px] text-gray-600 dark:text-gray-300">
                            <span v-if="flBackendLabel" :class="backendChipClass" data-test="fl-backend-chip">
                                {{ flBackendLabel }}
                            </span>
                            <span :class="[CHIP_CLASS, 'font-mono text-xs']" data-test="job-type-chip">
                                job_type: {{ jobType }}
                            </span>
                            <span
                                v-if="canUpload"
                                v-tippy="'Set job_type in your config.json to change'"
                                tabindex="0"
                                class="underline cursor-help decoration-dotted underline-offset-[3px]"
                            >
                                How to change
                            </span>
                        </div>
                    </div>

                    <!-- Scrolling with the list keeps the drop zone after the last file
                         instead of squeezing the list. -->
                    <div class="flex-1 min-h-0 overflow-y-auto">
                        <template v-if="loading">
                            <AiSkeleton class="w-full h-10" />
                            <AiSkeleton class="w-full h-6" />
                            <AiSkeleton class="w-full h-6" />
                            <AiSkeleton class="w-full h-6" />
                        </template>

                        <!-- Required files first, in the backend's order, as placeholders until
                             uploaded; then everything else. -->
                        <ul
                            v-else-if="fileRows.length"
                            role="list"
                        >
                            <li
                                v-for="row in fileRows"
                                :key="row.file?.id ?? `required-${row.name}`"
                                :data-test="row.file ? 'model-file-row' : 'required-file-placeholder'"
                                class="flex flex-row items-center gap-2.5 mx-4 py-2 border-t border-gray-100 dark:border-dark-border transition group"
                            >
                                <span
                                    v-if="!row.file"
                                    class="w-[18px] h-[18px] rounded-full bg-white dark:bg-dark-canvas border-[1.5px] border-dashed border-gray-300 dark:border-gray-600 shrink-0"
                                    data-test="file-status-missing"
                                    aria-label="Missing"
                                />
                                <div
                                    v-else
                                    class="relative flex items-center justify-end transition bg-white rounded-full w-5 h-5 dark:bg-dark-canvas ring-2 ring-offset-1 dark:ring-offset-dark-canvas shrink-0"
                                    :class="[
                                        row.file.status === FileUploadStatus.COMPLETED &&
                                            'ring-green-600/70 dark:ring-green-400',
                                        row.file.status === FileUploadStatus.UPLOADING &&
                                            'ring-gray-400/70 dark:ring-gray-600',
                                        row.file.status === FileUploadStatus.SCANNING &&
                                            'ring-amber-500/70 dark:ring-amber-400',
                                        row.file.status === FileUploadStatus.ERROR && 'ring-red-600/70 dark:ring-red-400',
                                        row.file.status === FileUploadStatus.INFECTED && 'ring-red-600 dark:ring-red-500',
                                    ]"
                                >
                                    <div class="relative flex items-center justify-center w-full h-full text-gray-700 bg-gray-100 border border-gray-300 rounded-full shadow dark:bg-dark-surface dark:text-gray-300 dark:border-dark-border-strong text-[10px]">
                                        <Transition name="fade" mode="out-in">
                                            <AiLoader v-if="row.file.status === FileUploadStatus.UPLOADING" small data-test="file-upload-status-uploading" aria-label="Uploading" />
                                            <!-- Its own mark, not the upload spinner: the bytes are safely
                                                 stored, what's pending is the release check (#52). Worded as
                                                 "checked" rather than "scanned for malware" because only
                                                 pickle-bearing files get a scan that can block release —
                                                 Python source instead gets a non-blocking Bandit pass (#877)
                                                 that never gates promotion, so a malware claim here would
                                                 still be false. -->
                                            <icon-ph-magnifying-glass-duotone
                                                v-else-if="row.file.status === FileUploadStatus.SCANNING"
                                                class="text-amber-600 dark:text-amber-400 animate-pulse"
                                                data-test="file-upload-status-scanning"
                                                aria-label="Checking file"
                                                title="Being checked before it can be used for training. Model checkpoints and other pickle files are also scanned for unsafe content."
                                            />
                                            <icon-ph-file-duotone v-else-if="row.file.status === FileUploadStatus.COMPLETED" data-test="file-upload-status-completed" aria-label="Ready" />
                                            <icon-ph-virus-duotone
                                                v-else-if="row.file.status === FileUploadStatus.INFECTED"
                                                class="text-red-600 dark:text-red-400"
                                                data-test="file-upload-status-infected"
                                                aria-label="Unsafe content detected"
                                                title="Unsafe content detected — the file was removed. Delete this entry and upload a fixed file."
                                            />
                                            <icon-ph-x-circle-duotone v-else-if="row.file.status === FileUploadStatus.ERROR" data-test="file-upload-status-error" aria-label="Upload failed" />
                                        </Transition>
                                    </div>
                                </div>
                                <div class="flex flex-col min-w-0 grow">
                                    <div class="flex flex-row items-center gap-2 min-w-0">
                                        <p
                                            data-test="file-name"
                                            class="font-mono text-[13px] truncate"
                                            :class="row.file
                                                ? 'font-semibold text-primary-500 dark:text-primary-200'
                                                : 'text-gray-500 dark:text-gray-300'"
                                            :title="row.name"
                                        >
                                            {{ row.name }}
                                        </p>
                                        <!-- Advisory only (#877) — the file is still COMPLETED and usable;
                                             this is a signal for the reviewer, not a rejection. -->
                                        <icon-ph-warning-fill
                                            v-if="row.file?.bandit_findings?.length"
                                            class="text-amber-500 dark:text-amber-400 shrink-0 w-4 h-4"
                                            data-test="file-bandit-findings-indicator"
                                            :aria-label="`${row.file.bandit_findings.length} static-analysis finding(s)`"
                                            :title="banditFindingsTooltip(row.file.bandit_findings)"
                                        />
                                    </div>
                                    <p
                                        class="text-xs"
                                        :class="row.file ? 'text-gray-500 dark:text-gray-300' : 'text-amber-700 dark:text-amber-400'"
                                    >
                                        {{ rowMeta(row) }}
                                    </p>
                                </div>
                                <div v-if="row.file" class="flex gap-2 shrink-0">
                                    <Transition name="fade">
                                        <AiButton
                                            v-if="!isViewer && row.file.status === FileUploadStatus.COMPLETED"
                                            small
                                            :loading="downloadingFile === row.name"
                                            :aria-label="`Download ${row.name}`"
                                            @click="() => downloadFile(row.name)"
                                        >
                                            <icon-ph-download-duotone />
                                        </AiButton>
                                    </Transition>
                                    <Transition name="fade">
                                        <AiButton v-if="canUpload && DELETABLE_STATUSES.includes(row.file.status)" small :aria-label="`Delete ${row.name}`" @click="() => confirmDeleteFile(row.name)">
                                            <icon-ph-trash-duotone class="text-red-500 dark:text-red-400" />
                                        </AiButton>
                                    </Transition>
                                </div>
                            </li>
                        </ul>

                        <div v-if="canUpload" class="px-4 pt-2 pb-4">
                            <FileUpload @new-files="uploadFile" />
                        </div>
                    </div>
                </div>
            </div>
        </AiCard>
    </section>
    <AiConfirmModal
        :dialog="confirmFileDeletion"
        continue-button-text="Delete File"
        :continue-action="deleteFile"
        :submitting="deletingFile"
        @close-modal="closeFileDeletion"
    >
        <template #confirmation>
            Are you sure you wish to delete <code class="font-black">{{ fileToDelete }}</code>?
            This file will not be available as part of model training.
        </template>
    </AiConfirmModal>
</template>

<script lang="ts" setup>
import JSZip from "jszip";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import AiConfirmModal from "@/components/AiModal/AiConfirmModal.vue";
import { usePermissions } from "@/composables/usePermissions";
import { DEMO_MODEL_FILES_ZIP_URLS, IS_DEMO } from "@/demo/bootstrap";
import { BanditFinding, FileInfo, FileUploadStatus } from "@/interfaces/model/types";
import { deleteModelFile,
    downloadModelFile,
    getModelFileDownloadUrl,
    processScannedFile } from "@/services/file-service";
import { JobType } from "@/services/model-service";
import { createPreSignedUrl, FileTooLargeError, uploadFile as uploadFileService } from "@/utils/file";
import { formatBytes, getRandomId } from "@/utils/helpers";
import { Snackbar } from "@/utils/snackbar";

import FileUpload from "./FileUpload.vue";

interface IModelUploadProps {
    files: FileInfo[],
    loading: boolean;
    canUpload: boolean;
    modelId: string;
    requiredFiles: string[];
    jobType: JobType;
    // "NVFlare" or "Flower", when known.
    flBackendLabel?: string;
    // True when the job-types map could not be loaded, so `jobType` is a default rather than this
    // model's actual job type.
    jobTypesError?: boolean;
}

const props = withDefaults(defineProps<IModelUploadProps>(), {
    flBackendLabel: undefined,
    jobTypesError: false
});

const emits = defineEmits(["uploaded", "deletedFile"]);

const { isViewer } = usePermissions();
const route = useRoute();
const internalFiles = ref<FileInfo[]>([]);
const uploadingFiles = ref<FileInfo[]>([]);
const filesAreUploading = ref<boolean>(false);
const confirmFileDeletion = ref<boolean>(false);
const deletingFile = ref<boolean>(false);
const downloadingFile = ref<string>();
const downloadingAll = ref<boolean>(false);
const fileToDelete = ref<string>();

// Terminal states whose row can be cleared. INFECTED is included so the user
// can dismiss a rejected file and upload a fixed one — its object is already
// gone from storage, so there is nothing left to protect.
const DELETABLE_STATUSES = [
    FileUploadStatus.COMPLETED,
    FileUploadStatus.ERROR,
    FileUploadStatus.INFECTED
];

// Advisory-only summary of a .py upload's Bandit findings (#877) — never
// gates use, so this is purely informational: visible to the uploader and to
// anyone else who later opens the model's file list.
const banditFindingsTooltip = (findings: BanditFinding[]): string => {
    const lines = findings.map(f => {
        const where = f.line_number ? ` (line ${f.line_number})` : "";

        return `${f.test_id ?? "?"} [${f.severity ?? "?"}]${where}: ${f.issue_text ?? "no detail"}`;
    });

    return `Static analysis flagged ${findings.length} advisory finding(s) — does not block use:\n${lines.join("\n")}`;
};

interface IFileRow {
    name: string;
    required: boolean;
    // Absent for a required file that has not been uploaded yet.
    file?: FileInfo;
}

const CHIP_CLASS = "px-2 py-0.5 font-semibold rounded-full bg-primary-100 text-primary-500 "
    + "dark:bg-dark-raised dark:text-primary-200";

// Each FL backend's brand colour, both light enough to need dark text: NVIDIA green, Flower yellow.
const BACKEND_CHIP_CLASSES: Record<string, string> = {
    NVFlare: "px-2 py-0.5 font-semibold rounded-full bg-[#76B900] text-ink",
    Flower: "px-2 py-0.5 font-semibold rounded-full bg-[#F2B705] text-ink"
};

const backendChipClass = computed(() => BACKEND_CHIP_CLASSES[props.flBackendLabel ?? ""] ?? CHIP_CLASS);

const allFiles = computed(() => internalFiles.value.concat(uploadingFiles.value));

const showRequired = computed(() => !props.jobTypesError && props.requiredFiles.length > 0);

const IN_FLIGHT_STATUSES = [FileUploadStatus.UPLOADING, FileUploadStatus.SCANNING];

// A required slot shows an upload in progress, else the server's copy, else a failed attempt. Any
// other entry for that name (say a failed re-upload over a good copy) gets its own row, so it
// never hides the copy training will use.
const slotFile = (name: string): FileInfo | undefined =>
    uploadingFiles.value.find(f => f.name === name && IN_FLIGHT_STATUSES.includes(f.status))
    ?? internalFiles.value.find(f => f.name === name)
    ?? uploadingFiles.value.find(f => f.name === name);

const fileRows = computed((): IFileRow[] => {
    const required = props.requiredFiles.map(name => ({
        name,
        required: true,
        file: slotFile(name)
    }));
    const shown = new Set(required.map(r => r.file));

    return [
        ...required,
        ...allFiles.value
            .filter(f => !shown.has(f))
            .map(f => ({
                name: f.name,
                required: false,
                file: f
            }))
    ];
});

// Only a file that passed its check counts, the same bar training holds it to.
const requiredUploadedCount = computed(() => props.requiredFiles.filter(name =>
    internalFiles.value.some(f => f.name === name && f.status === FileUploadStatus.COMPLETED)).length);

const requiredComplete = computed(() => requiredUploadedCount.value === props.requiredFiles.length);

const rowMeta = (row: IFileRow): string => {
    if (!row.file) return "Required · missing";
    const size = formatBytes(row.file.size);

    return row.required ? `Required · ${size}` : size;
};

// "Download all" is offered only when every visible file finished uploading
// + scanning. Any in-flight or errored file would either be missing from S3
// or unsafe to bundle, so we hide the button rather than partial-zip.
const canDownloadAll = computed(() =>
    allFiles.value.length > 0 && allFiles.value.every(f => f.status === FileUploadStatus.COMPLETED));

watch(props, () => {
    handleFiles();
},
{ deep: true });

onMounted(() => {
    handleFiles();
});

const handleFiles = () => {
    if (props.files?.length) {
        if(filesAreUploading.value) {
            uploadingFiles.value = uploadingFiles.value.filter(
                (file) => !props.files?.map(f => f.name).includes(file.name)
            );

            if(!uploadingFiles.value.length) {
                filesAreUploading.value = false;
            }
        }

        internalFiles.value = [...props.files];
    }
};

const uploadFile = async (fileList: FileList) => {

    Array.from(fileList).forEach((file) => {
        const fileInfo: FileInfo = {
            id: getRandomId(),
            name: file.name,
            size: file.size,
            status: FileUploadStatus.UPLOADING
        };

        uploadingFiles.value.push(fileInfo);
    });

    const blacklistedEnvVar = window.BLACKLISTED_MODEL_FILES;

    let blacklistedModelFiles: string[] = [];

    if (blacklistedEnvVar) {
        // Simple comma-separated list from BLACKLISTED_MODEL_FILES — see generate-window-js.sh.
        blacklistedModelFiles = blacklistedEnvVar.split(",").map(file => file.trim());
    }

    for (const file of fileList) {

        if (!file.name || blacklistedModelFiles?.includes(file.name) ) {

            Snackbar.error({
                text: "This file name is not supported as it's reserved by FLIP.",
                title: "Error"
            }, 12_000);

            uploadingFiles.value = uploadingFiles.value.filter(
                (uploadingFile) => uploadingFile.name !== file.name
            );

            continue;
        }

        try {
            const policy = await createPreSignedUrl(
                file,
                "/files/preSignedUrl/model",
                route.params["modelId"].toString()
            );

            if (!policy) {
                throw Error("No presigned upload policy returned");
            }

            if (file.size > policy.maxBytes) {
                throw new FileTooLargeError(policy.maxBytes, file.size);
            }

            await uploadFileService(
                file,
                policy
            );

            filesAreUploading.value = true;

            Snackbar.success({
                title: "File Uploaded!",
                text: `${file.name} has been uploaded successfully.`
            });

            const fileToUpdate = uploadingFiles.value.find((uploadingFile) => uploadingFile.name === file.name);

            if(fileToUpdate) {
                fileToUpdate.status = FileUploadStatus.SCANNING;
            }

            // Register the upload so the server scans it. The scan itself runs
            // server-side and its outcome (COMPLETED / INFECTED / ERROR) reaches
            // us through the parent's model polling — there is nothing to wait
            // for here.
            const modelId = route.params["modelId"].toString();

            await processScannedFile(
                `/files/process-scanned-file/${modelId}/${file.name}`
            );

        } catch (error) {
            const erroredFile = uploadingFiles.value.find((uploadingFile) => uploadingFile.name === file.name);

            if(erroredFile) {
                erroredFile.status = FileUploadStatus.ERROR;
            }

            if (error instanceof FileTooLargeError) {
                Snackbar.error({
                    title: "File too large",
                    text: `${file.name} is ${formatBytes(error.actualBytes)} which exceeds the `
                        + `${formatBytes(error.limitBytes)} limit.`
                }, 12_000);
            } else {
                const rejectionReason = getRejectionReason(error);

                if (rejectionReason) {
                    Snackbar.error({
                        title: "File not accepted",
                        text: rejectionReason
                    }, 12_000);
                } else {
                    Snackbar.error({
                        title: "Error uploading file",
                        text: "There was an error uploading this file. Please try again."
                    });
                }
            }
        }
    }

    emits("uploaded", true);
};

/**
 * Pull the server's own explanation out of a 400 so the user is told what to
 * fix (e.g. which file types are allowed) rather than "please try again".
 * Only 400s are surfaced verbatim — other statuses can carry internal detail.
 */
const getRejectionReason = (error: unknown): string | undefined => {
    const response = (error as { response?: { status?: number, data?: { detail?: unknown } } })?.response;

    if (response?.status !== 400) return undefined;

    return typeof response.data?.detail === "string" ? response.data.detail : undefined;
};

const confirmDeleteFile = (name: string) => {
    confirmFileDeletion.value = true;
    fileToDelete.value = name;
};

const deleteFile = async () => {
    deletingFile.value = true;
    await deleteModelFile(`/files/model/${props.modelId}/${fileToDelete.value}`);
    internalFiles.value = [...internalFiles.value.filter(f => f.name !== fileToDelete.value)];
    emits("deletedFile");
    deletingFile.value = false;
    closeFileDeletion();
};

const closeFileDeletion = () => {
    confirmFileDeletion.value = false;
    fileToDelete.value = undefined;
};

// Caps how many files download-all fetches at once so one huge file doesn't
// starve/timeout the rest. Simple sequential batching (no worker-pool): a
// batch waits for its slowest member before the next starts, which is an
// acceptable tradeoff for the simplicity of no new dependency.
// Note the zip is assembled in memory (every blob + the archive), so
// download-all is not suitable for multi-GiB files — the per-file button
// streams via the browser instead and has no such bound.
const DOWNLOAD_ALL_CONCURRENCY = 3;

const downloadAllAsZip = async () => {
    if (downloadingAll.value) return;

    // Public Ark+ demo: each recorded run's file bundle (including the ~795 MB
    // checkpoints) is a pre-built zip served through the CloudFront
    // demo-assets behaviour — the in-browser mock can't stream file bodies of
    // that size. Vite inlines IS_DEMO, so normal builds keep the JSZip path only.
    if (IS_DEMO) {
        const zipUrl = DEMO_MODEL_FILES_ZIP_URLS[props.modelId];
        if (!zipUrl) {
            // An id missing from the map means the bundle was never staged for
            // this model. Say so: silently returning left the button looking
            // broken, with no snackbar and no spinner, unlike the real path
            // directly below (FLIP#794 review).
            Snackbar.error({
                title: "Download unavailable",
                text: "No file bundle was staged for this model in the demo."
            });

            return;
        }

        const link = document.createElement("a");
        link.href = zipUrl;
        // Without `download` the browser follows Content-Disposition, and any
        // response served as HTML (e.g. a mis-provisioned behaviour falling
        // through to the SPA rewrite) navigates the tab instead of downloading.
        link.download = zipUrl.split("/").pop() ?? "model-files.zip";
        document.body.appendChild(link);
        link.click();
        link.remove();

        return;
    }
    downloadingAll.value = true;
    try {
        const all = allFiles.value;
        const zip = new JSZip();
        for (let i = 0; i < all.length; i += DOWNLOAD_ALL_CONCURRENCY) {
            const batch = all.slice(i, i + DOWNLOAD_ALL_CONCURRENCY);
            await Promise.all(batch.map(async file => {
                const path = `/files/model/${props.modelId}/${encodeURIComponent(file.name)}`;
                const blob = await downloadModelFile(path);
                zip.file(file.name, blob);
            }));
        }
        const archive = await zip.generateAsync({ type: "blob" });
        const url = URL.createObjectURL(archive);
        const a = document.createElement("a");
        a.href = url;
        a.download = `model-${props.modelId}-files.zip`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    } catch {
        Snackbar.error({
            title: "Download failed",
            text: "Could not bundle the model files into a zip. Please try again."
        });
    } finally {
        downloadingAll.value = false;
    }
};

const downloadFile = async (fileName: string) => {
    downloadingFile.value = fileName;

    try {
        const path = `/files/model/${props.modelId}/${encodeURIComponent(fileName)}`;
        const { url } = await getModelFileDownloadUrl(path);

        // Navigate straight to the presigned URL rather than fetching into a
        // Blob: S3 serves it with `Content-Disposition: attachment` (set
        // server-side), so the browser's download manager streams it to disk.
        // No in-memory copy — files up to MAX_MODEL_FILE_BYTES (5 GiB) stay
        // downloadable where a Blob would exhaust the tab. The catch below
        // still covers the likely failures (auth/404/500 from flip-api); an
        // S3-side rejection after a fresh URL is rare enough to accept the
        // browser's own error surface for it.
        const a = document.createElement("a");
        a.href = url;
        document.body.appendChild(a);
        a.click();
        a.remove();
    } catch {
        Snackbar.error({
            title: "Download failed",
            text: `Could not download ${fileName}. Please try again.`
        });
    } finally {
        downloadingFile.value = undefined;
    }
};
</script>
