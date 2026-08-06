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
    <div class="flex flex-row items-center justify-end gap-3 w-full">
        <!-- Below lg the labels hide (icon + tooltip only) so the buttons stop
             squashing the truncating model title in the page header. -->
        <button
            type="button"
            data-test="stop-training-btn"
            class="inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed bg-red-600 text-white hover:bg-red-700 disabled:hover:bg-red-600"
            :disabled="!canStopTraining"
            :title="stopActionLabel"
            :aria-label="stopActionLabel"
            @click="dialogStopTraining = true"
        >
            <icon-mdi-stop class="w-4 h-4" aria-hidden="true" />
            <span class="hidden lg:inline">{{ stopActionLabel }}</span>
        </button>

        <AiButton
            light
            data-test="download-results-btn"
            aria-label="Download Results"
            tooltip="Download Results"
            :disabled="!canDownloadResults"
            @click="downloadTrainingResults"
        >
            <icon-ph-download class="w-4 h-4 lg:mr-2" aria-hidden="true" />
            <span class="hidden lg:inline">Download Results</span>
        </AiButton>
    </div>

    <AiConfirmModal
        :dialog="dialogStopTraining"
        :confirmation-text="stopConfirmationText"
        close-button-text="Cancel"
        :continue-button-text="stopActionLabel"
        :continue-action="stopTrainingAction"
        :submitting="stopTrainingSubmitting"
        @close-modal="dialogStopTraining = false;"
    />
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute } from "vue-router";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiConfirmModal from "@/components/AiModal/AiConfirmModal.vue";
import { getDownloadUrlForResults, ModelStatusEnum, stopTraining } from "@/services/model-service";
import { Snackbar } from "@/utils/snackbar";

interface ITrainingActionsProps {
    status: ModelStatusEnum;
}

const props = defineProps<ITrainingActionsProps>();

const route = useRoute();
const dialogStopTraining = ref(false);
const stopTrainingSubmitting = ref(false);

const canDownloadResults = computed(() => props.status === ModelStatusEnum.RESULTS_UPLOADED);

// While the model is still queued (INITIATED — before the fl-server reports PREPARED) the same
// stop endpoint aborts the job before it runs: it is dequeued and its net released, so the
// action reads "Abort job" rather than "Stop Training". PENDING never renders this menu (page
// gate). "Pre-running", not "pre-training": the ML term "pretraining" means something else.
const isPreRunning = computed(() => props.status === ModelStatusEnum.INITIATED);

const canStopTraining = computed(() =>
    props.status >= ModelStatusEnum.INITIATED && props.status < ModelStatusEnum.RESULTS_UPLOADED
);

const stopActionLabel = computed(() => (isPreRunning.value ? "Abort job" : "Stop Training"));

const stopConfirmationText = computed(() => (isPreRunning.value
    ? "This model has not started training yet. Are you sure you want to abort this job? "
      + "It will be removed from the queue and its training slot will be released."
    : "Are you sure you want to stop training this model?"));

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
            text: isPreRunning.value ? "Failed to abort job" : "Failed to stop training"
        });
    }
};

const downloadTrainingResults = async () => {
    try {
        const urls = await getDownloadUrlForResults(modelId);
        if (!urls.length) {
            Snackbar.error({
                title: "No results available",
                text: "No downloadable result files were found for this model."
            });

            return;
        }
        urls.forEach((url) => {
            const element = document.createElement("a");
            element.setAttribute("href", url);
            element.click();
            element.remove();
        });
    }
    catch {
        Snackbar.error({
            title: "Unable to download results",
            text: "There was a problem when downloading the results for this model."
        });
    }
};
</script>
