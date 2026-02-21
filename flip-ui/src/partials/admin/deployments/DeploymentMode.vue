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
    <div class="flex flex-col w-full grow">
        <transition name="fade" mode="out-in">
            <div v-if="!details.banner" class="py-36">
                <AiLoader />
            </div>
            <div v-else class="flex flex-col p-4 space-y-4 grow">
                <div
                    class="relative "
                >
                    <icon-ph-download-duotone
                        class="w-16 h-16 mb-8 transition"
                        :class="[details.deploymentMode ? 'text-primary-600 dark:text-green-400' : 'text-gray-300']"
                    />
                    <h3 class="mt-2 text-3xl font-semibold font-heading">
                        Deployment Mode is <span
                            class="font-black underline uppercase transition decoration-4 decoration-solid underline-offset-8"
                            :class="[details.deploymentMode ? 'decoration-primary-600 dark:decoration-green-400' : 'decoration-transparent']"
                            data-test="deployment-mode-status-text"
                        >
                            {{ details.deploymentMode ? 'Enabled' : 'Disabled' }}
                        </span>
                    </h3>
                    <p class="max-w-2xl my-6 text-gray-400">
                        Enabling <strong class="font-bold">deployment mode</strong> will disable core functionality
                        across the platform whilst a deployment is in progress. You probably want to enable a site
                        banner whilst this is enabled.
                    </p>
                    <AiButton primary :loading="loadingButton" @click="confirm">
                        {{ !details.deploymentMode ? 'Enable' : 'Disable' }} Deployment Mode
                    </AiButton>
                </div>
            </div>
        </transition>
    </div>
    <AiConfirmModal
        :dialog="confirmDialog"
        :confirmation-text="confirmationText"
        close-button-text="Cancel"
        title="Enable deployment mode?"
        continue-button-text="Enable Deployment Mode"
        :continue-action="toggleDeploymentMode"
        :submitting="loadingButton"
        @close-modal="close"
    />
</template>

<script setup lang="ts">
import { ref } from "vue";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiConfirmModal from "@/components/AiModal/AiConfirmModal.vue";
import { useSiteDetailsStore } from "@/store/siteDetailsStore";

const details = useSiteDetailsStore();
const loadingButton = ref(false);
const confirmDialog = ref(false);

const confirmationText = `
    Are you sure you want to enable <strong>deployment mode</strong>?
    <p class='mt-2'>
        This will disable core functionality across the platform.
        You probably want to enable a site banner whilst this is enabled.
    </p>`;

const confirm = async () => {
    if(details.deploymentMode) {
        toggleDeploymentMode();

        return;
    }

    confirmDialog.value = true;
};

const close = () => {
    confirmDialog.value = false;
};

const toggleDeploymentMode = async () => {
    if(loadingButton.value) {
        return;
    }

    loadingButton.value = true;

    await details.updateDeploymentMode(!details.deploymentMode);

    loadingButton.value = false;
    confirmDialog.value = false;
};

</script>
