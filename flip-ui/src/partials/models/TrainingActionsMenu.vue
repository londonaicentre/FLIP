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
    <Menu v-slot="{ open }" as="div" class="relative inline-block text-left">
        <div>
            <MenuButton>
                <AiButton
                    text-primary
                    data-test="user-btn"
                    :class="{ 'ring-2 ring-primary-500 ring-offset-2 rounded dark:ring-offset-gray-900 dark:ring-primary-400': open }"
                >
                    Actions
                    <icon-mdi-chevron-down class="w-4 h-4 ml-2 -mr-1 text-gray-600 dark:text-gray-400" />
                </AiButton>
            </MenuButton>
        </div>

        <transition
            enter-active-class="transition duration-100 ease-out"
            enter-from-class="transform scale-95 opacity-0"
            enter-to-class="transform scale-100 opacity-100"
            leave-active-class="transition duration-75 ease-in"
            leave-from-class="transform scale-100 opacity-100"
            leave-to-class="transform scale-95 opacity-0"
        >
            <MenuItems
                class="absolute right-0 w-56 z-[1] mt-2 origin-top-right bg-white dark:bg-gray-900 dark:divide-gray-700 dark:ring-white/20 divide-y divide-gray-100 rounded-md shadow-lg ring-1 ring-black ring-opacity-5 focus:outline-none"
            >
                <div class="py-1">
                    <MenuItem v-slot="{ active }" :disabled="!canStopTraining">
                        <button
                            :class="[
                                active ? 'bg-gray-100 dark:bg-gray-800' : 'text-gray-600 dark:text-gray-400',
                                !canStopTraining ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
                                'group flex rounded-md items-center w-full px-3 py-2 text-sm transition font-semibold',
                            ]"
                            :disabled="!canStopTraining"
                            data-test="stop-training-btn"
                            @click.capture="() => dialogStopTraining = true"
                        >
                            <icon-mdi-stop
                                class="w-5 h-5 mr-3 text-gray-500 transition group-hover:text-gray-600 dark:text-gray-300 dark:group-hover:text-gray-300"
                                aria-hidden="true"
                            />
                            Stop Training
                        </button>
                    </MenuItem>
                </div>
                <div class="py-1">
                    <MenuItem v-slot="{ active }" :disabled="!canDownloadResults">
                        <button
                            :class="[
                                active ? 'bg-gray-100 dark:bg-gray-800' : 'text-gray-600 dark:text-gray-400',
                                !canDownloadResults ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
                                'group flex rounded-md items-center w-full px-3 py-2 text-sm transition font-semibold',
                            ]"
                            @click.capture="downloadTrainingResults"
                        >
                            <icon-heroicons-outline-download
                                class="w-5 h-5 mr-3 text-gray-500 transition group-hover:text-gray-600 dark:text-gray-300 dark:group-hover:text-gray-300"
                                aria-hidden="true"
                            />
                            <div class="flex flex-col items-start dark:text-gray-300">
                                <div>Download Results</div>
                                <div class="text-gray-400 dark:text-gray-500">
                                    where available
                                </div>
                            </div>
                        </button>
                    </MenuItem>
                </div>
            </MenuItems>
        </transition>
    </Menu>
    <AiConfirmModal
        :dialog="dialogStopTraining"
        confirmation-text="Are you sure you want to stop training this model?"
        close-button-text="Cancel"
        continue-button-text="Stop Training"
        :continue-action="stopTrainingAction"
        :submitting="stopTrainingSubmitting"
        @close-modal="dialogStopTraining = false;"
    />
</template>

<script setup lang="ts">
import { Menu, MenuButton, MenuItem, MenuItems } from "@headlessui/vue";
import { computed, ref } from "vue";
import { useRoute } from "vue-router";

import AiButton from "@/components/AiButton/AiButton.vue";
import { getDownloadUrlForResults, ModelStatusEnum, stopTraining } from "@/services/model-service";
import { Snackbar } from "@/utils/snackbar";

interface ITrainingActionsProps {
    status: ModelStatusEnum;
}

const props = defineProps<ITrainingActionsProps>();

const route = useRoute();
const dialogStopTraining = ref(false);
const stopTrainingSubmitting = ref(false);

const canDownloadResults = computed(() =>
    [
        ModelStatusEnum.RESULTS_UPLOADED,
        ModelStatusEnum.STOPPED,
        ModelStatusEnum.ERROR
    ].includes(props.status)
);

const canStopTraining = computed(() =>
    props.status >= ModelStatusEnum.PREPARED && props.status < ModelStatusEnum.RESULTS_UPLOADED
);

const modelId = route.params["modelId"].toString();

const stopTrainingAction = async () => {
    try {
        stopTrainingSubmitting.value = true;
        await stopTraining(modelId);
        dialogStopTraining.value = false;
        stopTrainingSubmitting.value = false;
    }
    catch {
        dialogStopTraining.value = false;
        stopTrainingSubmitting.value = false;
        Snackbar.error({
            title: "Something went wrong!",
            text: "Failed to stop training"
        });
    }
};

const downloadTrainingResults = async () => {
    const urls = await getDownloadUrlForResults(
        modelId
    );
    if (!urls.length) {
        Snackbar.error({
            title: "Unable to download results",
            text: "There was a problem when downloading the results for this model."
        });

        return;
    }
    urls.forEach((url) => {
        const element = document.createElement("a");
        element.setAttribute("href", url);
        element.click();
        element.remove();
    });
};
</script>
