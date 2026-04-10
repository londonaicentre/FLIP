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
    <div
        class="border-2 ring-2 ring-offset-4 border-dashed rounded-lg bg-primary-100 dark:bg-gray-800 overflow-hidden border-primary-500 dark:border-primary-400 transition dark:ring-offset-gray-900 ring-offset-white"
        :class="{ 'dark:ring-primary-400 ring-primary-600': dragover, 'ring-transparent': !dragover }"
        @drop.prevent="emitDroppedFile($event)"
        @dragover.prevent="dragover = true"
        @dragenter.prevent="dragover = false"
        @dragleave.prevent="dragover = false"
    >
        <AiAlert
            variant="info"
            :text="requiredFilesMessage"
            class="w-full"
            :rounded="false"
            :bordered="false"
        />

        <div
            class="flex items-center justify-center px-4 py-4 mx-auto grow h-[150px]"
        >
            <icon-mdi-cloud-upload-outline class="hidden w-16 h-16 text-gray-200 dark:text-gray-500 sm:block" />
            <input
                ref="fileUpload"
                type="file"
                data-test="upload-file-input"
                multiple
                hidden
                @change.capture="emitChoosenFiles"
            >
            <p class="m-0 ml-4 text-sm text-center text-black dark:text-gray-400">
                Drag & drop files or <br>
                <a
                    class="font-semibold underline cursor-pointer hover:text-primary-500 dark:hover:text-primary-200"
                    data-test="upload-file-btn"
                    @click="openFilesNativeDialog()"
                >browse</a>
                to upload
            </p>
        </div>
    </div>
</template>

<script lang="ts" setup>
import { computed, ref } from "vue";

import AiAlert from "@/components/AiAlert/AiAlert.vue";
import { JobTypes } from "@/services/model-service";

interface IFileUploadProps {
    requiredFiles: string[];
    jobType: JobTypes;
}

const props = defineProps<IFileUploadProps>();

const emit = defineEmits<{
    (e: "newFiles", files: FileList): void;
}>();

const dragover = ref(false);
const fileUpload = ref<HTMLInputElement | null>(null);

/**
 * Generates the required files message based on job type
 */
const requiredFilesMessage = computed(() => {
    const filesList = props.requiredFiles.join(", ");
    return `Your current job type is: <strong><code>${props.jobType}</code></strong>. If you want to change it, add it as a <code>job_type</code> variable in your <code>config.json</code> file.<br/>Required files: ${filesList}`;
});

const openFilesNativeDialog = () => {
    if (fileUpload.value) {
        fileUpload.value.click();
    }
};

const emitChoosenFiles = (event: Event) => {
    const target = event.target as HTMLInputElement;
    if (target && target.files) {
        emit("newFiles", target.files);
    }
};

const emitDroppedFile = (event: DragEvent) => {
    dragover.value = false;
    if (event && event.dataTransfer && event.dataTransfer.files) {
        emit("newFiles", event.dataTransfer.files);
    }
};
</script>
