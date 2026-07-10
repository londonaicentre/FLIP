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
    <section>
        <AiCard>
            <div>
                <div class="flex items-center gap-3 p-4">
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
                <div class="border-t border-gray-200 dark:border-dark-border">
                    <AiAlert
                        v-if="canUpload"
                        variant="info"
                        class="w-full"
                        :rounded="false"
                        :bordered="false"
                    >
                        <div class="text-xs leading-snug">
                            Your current job type is: <strong><code>{{ jobType }}</code></strong>.
                            If you want to change it, add it as a <code>job_type</code> variable in your
                            <code>config.json</code> file.
                        </div>
                    </AiAlert>
                    <div v-if="canUpload" class="flow-root p-2">
                        <div class="flex flex-col">
                            <template v-if="loading">
                                <AiSkeleton class="w-full h-32 border-2 border-dashed rounded-lg border-primary-300" />
                            </template>
                            <FileUpload @new-files="uploadFile" />
                        </div>
                    </div>
                    <template v-if="loading">
                        <AiSkeleton class="w-full h-10" />
                        <AiSkeleton class="w-full h-6" />
                        <AiSkeleton class="w-full h-6" />
                        <AiSkeleton class="w-full h-6" />
                    </template>


                    <ul
                        v-else-if="internalFiles.concat(uploadingFiles).length"
                        role="list"
                        class="border-t divide-y divide-gray-200 border-t-gray-200 dark:divide-dark-border dark:border-t-gray-700"
                    >
                        <li v-for="file in internalFiles.concat(uploadingFiles)" :key="file.id" class="flex flex-row items-center gap-3 px-4 py-1.5 transition group">
                            <div
                                class="relative flex items-center justify-end transition bg-white rounded-full w-5 h-5 dark:bg-dark-canvas ring-2 ring-offset-1 dark:ring-offset-dark-canvas shrink-0"
                                :class="[
                                    file.status === FileUploadStatus.COMPLETED &&
                                        'ring-green-600/70 dark:ring-green-400',
                                    [FileUploadStatus.UPLOADING, FileUploadStatus.SCANNING].includes(file.status) &&
                                        'ring-gray-400/70 dark:ring-gray-600',
                                    file.status === FileUploadStatus.ERROR && 'ring-red-600/70 dark:ring-red-400',
                                ]"
                            >
                                <div class="relative flex items-center justify-center w-full h-full text-gray-700 bg-gray-100 border border-gray-300 rounded-full shadow dark:bg-dark-surface dark:text-gray-300 dark:border-dark-border-strong text-[10px]">
                                    <Transition name="fade" mode="out-in">
                                        <AiLoader v-if="file.status === FileUploadStatus.UPLOADING" small data-test="file-upload-status-uploading" />
                                        <AiLoader v-else-if="file.status === FileUploadStatus.SCANNING" small data-test="file-upload-status-scanning" />
                                        <icon-ph-file-duotone v-else-if="file.status === FileUploadStatus.COMPLETED" data-test="file-upload-status-completed" />
                                        <icon-ph-x-circle-duotone v-else-if="file.status === FileUploadStatus.ERROR" data-test="file-upload-status-error" />
                                    </Transition>
                                </div>
                            </div>
                            <div class="flex flex-row items-baseline gap-2 min-w-0 grow">
                                <p
                                    class="text-sm font-semibold text-primary-600 dark:text-primary-200 truncate"
                                    :title="file.name"
                                >
                                    {{ file.name }}
                                </p>
                                <div class="text-xs font-mono text-gray-500 dark:text-gray-300 shrink-0">
                                    {{ formatBytes(file.size) }}
                                </div>
                            </div>
                            <div class="flex gap-2 shrink-0">
                                <Transition name="fade">
                                    <AiButton
                                        v-if="!isViewer && file.status === FileUploadStatus.COMPLETED"
                                        small
                                        :loading="downloadingFile === file.name"
                                        :aria-label="`Download ${file.name}`"
                                        @click="() => downloadFile(file.name)"
                                    >
                                        <icon-ph-download-duotone />
                                    </AiButton>
                                </Transition>
                                <Transition name="fade">
                                    <AiButton v-if="canUpload && (file.status === FileUploadStatus.COMPLETED || file.status === FileUploadStatus.ERROR)" small :aria-label="`Delete ${file.name}`" @click="() => confirmDeleteFile(file.name)">
                                        <icon-ph-trash-duotone class="text-red-500 dark:text-red-400" />
                                    </AiButton>
                                </Transition>
                            </div>
                        </li>
                    </ul>
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

import AiAlert from "@/components/AiAlert/AiAlert.vue";
import AiButton from "@/components/AiButton/AiButton.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import AiConfirmModal from "@/components/AiModal/AiConfirmModal.vue";
import { usePermissions } from "@/composables/usePermissions";
import { FileInfo, FileUploadStatus } from "@/interfaces/model/types";
import { deleteModelFile, downloadModelFile, processScannedFile } from "@/services/file-service";
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
}

const props = defineProps<IModelUploadProps>();

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

// "Download all" is offered only when every visible file finished uploading
// + scanning. Any in-flight or errored file would either be missing from S3
// or unsafe to bundle, so we hide the button rather than partial-zip.
const canDownloadAll = computed(() => {
    const all = internalFiles.value.concat(uploadingFiles.value);

    return all.length > 0 && all.every(f => f.status === FileUploadStatus.COMPLETED);
});

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

            // Process the scanned file after upload

            // wait 3 seconds
            await new Promise(resolve => setTimeout(resolve, 3000));

            // Define modelId as route.params["modelId"].toString()
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
                Snackbar.error({
                    title: "Error uploading file",
                    text: "There was an error uploading this file. Please try again."
                });
            }
        }
    }

    // Once files have uploaded, wait 10s before getting model again.
    setTimeout(() => {
        emits("uploaded", true);
    }, 10_000);

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

const downloadAllAsZip = async () => {
    if (downloadingAll.value) return;
    downloadingAll.value = true;
    try {
        const all = internalFiles.value.concat(uploadingFiles.value);
        const zip = new JSZip();
        // Fetch in parallel so the user isn't waiting on serial round-trips.
        // JSZip handles ordering inside the archive itself.
        await Promise.all(all.map(async file => {
            const path = `/files/model/${props.modelId}/${encodeURIComponent(file.name)}`;
            const blob = await downloadModelFile(path);
            zip.file(file.name, blob);
        }));
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
        const blob = await downloadModelFile(path);

        const blobUrl = URL.createObjectURL(blob);

        const a = document.createElement("a");
        a.href = blobUrl;
        a.download = fileName;
        document.body.appendChild(a);
        a.click();
        a.remove();

        URL.revokeObjectURL(blobUrl);
    } finally {
        downloadingFile.value = undefined;
    }
};
</script>
