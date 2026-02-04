<!--
    Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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
                <h2 class="p-4 text-lg font-semibold leading-loose font-heading">
                    Model Files
                </h2>
                <div class="border-t border-gray-200 dark:border-gray-700">
                    <div v-if="canUpload" class="flow-root p-2">
                        <div class="flex flex-col">
                            <template v-if="loading">
                                <AiSkeleton class="w-full h-32 border-2 border-dashed rounded-lg border-primary-300" />
                            </template>
                            <FileUpload 
                                :required-files="requiredFiles" 
                                :job-type="jobType" 
                                @new-files="uploadFile" 
                            />
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
                        class="border-t divide-y divide-gray-200 border-t-gray-200 dark:divide-gray-700 dark:border-t-gray-700"
                    >
                        <li v-for="file in internalFiles.concat(uploadingFiles)" :key="file.id" class="flex flex-row items-center gap-4 px-4 transition group">
                            <div
                                class="relative flex flex-col items-center justify-end transition bg-white rounded-full w-7 h-7 dark:bg-gray-900 ring-2 ring-offset-2 dark:ring-offset-gray-900 shrink-0"
                                :class="[
                                    file.status === FileUploadStatus.COMPLETED &&
                                        'ring-green-600/70 dark:ring-green-400',
                                    [FileUploadStatus.UPLOADING, FileUploadStatus.SCANNING].includes(file.status) &&
                                        'ring-gray-400/70 dark:ring-gray-600',
                                    file.status === FileUploadStatus.ERROR && 'ring-red-600/70 dark:ring-red-400',
                                ]"
                            >
                                <div class="relative flex items-center justify-center w-full text-gray-700 bg-gray-100 border border-gray-300 rounded-full shadow dark:bg-gray-800 dark:text-gray-300 grow dark:border-gray-500">
                                    <Transition name="fade" mode="out-in">
                                        <AiLoader v-if="file.status === FileUploadStatus.UPLOADING || file.status === FileUploadStatus.SCANNING" small />
                                        <icon-ph-file-duotone v-else-if="file.status === FileUploadStatus.COMPLETED" />
                                        <icon-ph-x-circle-duotone v-else-if="file.status === FileUploadStatus.ERROR" />
                                    </Transition>
                                </div>
                            </div>
                            <div class="flex flex-col items-start w-full py-4 truncate shrink">
                                <p
                                    class="text-sm font-bold text-primary-600 dark:text-primary-200 shrink line-clamp-1"
                                >
                                    {{ file.name }}
                                </p>
                                <div class="mr-2 text-sm text-gray-400 truncate shrink-0">
                                    {{ formatBytes(file.size) }}
                                </div>
                            </div>
                            <div class="flex gap-2 grow">
                                <Transition name="fade">
                                    <AiButton v-if="file.status === FileUploadStatus.COMPLETED" small :loading="downloadingFile === file.name" @click="() => downloadFile(file.name)">
                                        <icon-ph-download-duotone />
                                    </AiButton>
                                </Transition>
                                <Transition name="fade">
                                    <AiButton v-if="canUpload && file.status === FileUploadStatus.COMPLETED || file.status === FileUploadStatus.ERROR" small @click="() => confirmDeleteFile(file.name)">
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
        :confirmation-text="deleteFileConfirmationText"
        continue-button-text="Delete File"
        :continue-action="deleteFile"
        :submitting="deletingFile"
        @close-modal="closeFileDeletion"
    />
</template>

<script lang="ts" setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import AiConfirmModal from "@/components/AiModal/AiConfirmModal.vue";
import { FileInfo, FileUploadStatus } from "@/interfaces/model/types";
import { deleteModelFile, downloadModelFile, processScannedFile } from "@/services/file-service";
import { JobTypes } from "@/services/model-service";
import { createPreSignedUrl, uploadFile as uploadFileService } from "@/utils/file";
import { formatBytes, getRandomId } from "@/utils/helpers";
import { Snackbar } from "@/utils/snackbar";

import FileUpload from "./FileUpload.vue";

interface IModelUploadProps {
    files: FileInfo[],
    loading: boolean;
    canUpload: boolean;
    modelId: string;
    requiredFiles: string[];
    jobType: JobTypes;
}

const props = defineProps<IModelUploadProps>();

const emits = defineEmits(["uploaded", "deletedFile"]);

const route = useRoute();
const internalFiles = ref<FileInfo[]>([]);
const uploadingFiles = ref<FileInfo[]>([]);
const filesAreUploading = ref<boolean>(false);
const confirmFileDeletion = ref<boolean>(false);
const deletingFile = ref<boolean>(false);
const downloadingFile = ref<string>();
const fileToDelete = ref<string>();

const deleteFileConfirmationText = computed(() =>
    `Are you sure you wish to delete <code class='font-black'>${fileToDelete.value}</code>?
This file will not be available as part of model training.`
);

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

    const devMode = process.env.NODE_ENV === "development";

    const blacklistedEnvVar = devMode ? process.env.VITE_BLACKLISTED_MODEL_FILES : window.BLACKLISTED_MODEL_FILES;
    
    let blacklistedModelFiles: string[] = [];
    
    if (blacklistedEnvVar) {
        // Handling model files: before, this was using JSON parsing. Now it takes a simple string of files. 
        blacklistedModelFiles = blacklistedEnvVar.split(',').map(file => file.trim());
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
            const url = await createPreSignedUrl(
                file,
                "/files/preSignedUrl/model",
                route.params["modelId"].toString()
            );

            if (!url) {
                throw Error("No presigned URL returned 😢");
            }

            await uploadFileService(
                file,
                url
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

            Snackbar.error({
                title: "Error uploading file",
                text: "There was an error uploading this file. Please try again."
            });
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
