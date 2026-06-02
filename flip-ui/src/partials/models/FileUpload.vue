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
        v-if="!isViewer"
        class="border-2 ring-2 ring-offset-4 border-dashed rounded-lg bg-primary-100 dark:bg-gray-800 overflow-hidden border-primary-500 dark:border-primary-400 transition dark:ring-offset-gray-900 ring-offset-white"
        :class="{ 'dark:ring-primary-400 ring-primary-600': dragover, 'ring-transparent': !dragover }"
        @drop.prevent="emitDroppedFile($event)"
        @dragover.prevent="dragover = true"
        @dragenter.prevent="dragover = false"
        @dragleave.prevent="dragover = false"
    >
        <div
            class="flex items-center justify-center min-h-24 gap-3 px-4 py-6 mx-auto grow"
        >
            <icon-mdi-cloud-upload-outline class="hidden w-8 h-8 text-gray-300 dark:text-gray-500 sm:block" />
            <input
                ref="fileUpload"
                type="file"
                data-test="upload-file-input"
                multiple
                hidden
                @change.capture="emitChoosenFiles"
            >
            <p class="m-0 text-sm text-center text-black dark:text-gray-400">
                Drag &amp; drop or
                <a
                    class="font-semibold underline cursor-pointer hover:text-primary-500 dark:hover:text-primary-200"
                    data-test="upload-file-btn"
                    @click="openFilesNativeDialog()"
                >browse</a>
                to upload files
            </p>
        </div>
    </div>
</template>

<script lang="ts" setup>
import { ref } from "vue";

import { usePermissions } from "@/composables/usePermissions";

const emit = defineEmits<{
    (e: "newFiles", files: FileList): void;
}>();

const { isViewer } = usePermissions();

const dragover = ref(false);
const fileUpload = ref<HTMLInputElement | null>(null);

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
