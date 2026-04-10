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

<!-- eslint-disable vue/multi-word-component-names -->
<template>
    <Form v-slot="{ errors }" class="w-full h-full" :validation-schema="schema" @submit="initTraining">
        <AiCard class="flex flex-col h-full overflow-hidden">
            <div class="flex flex-col h-full">
                <div class="flex flex-col">
                    <div class="p-4 md:flex md:items-center md:justify-between">
                        <div class="flex-1 min-w-0">
                            <h1 class="text-lg font-semibold font-heading">
                                <span>Training</span>
                            </h1>
                        </div>
                        <div class="flex mt-4 md:mt-0 md:ml-4">
                            <AiButton
                                primary
                                type="submit"
                                :disabled="!canTrain"
                                :loading="formSubmitting"
                                data-test="initiate-training-btn"
                                class="mr-2"
                            >
                                Initiate Training
                            </AiButton>
                            <TrainingActionsMenu :status="getStatus" />
                        </div>
                    </div>
                </div>
                <div v-if="getStatus === ModelStatusEnum.PENDING" class="flex flex-col h-full overflow-y-auto">
                    <span>
                        <AiAlert
                            v-if="!allFilesUploaded"
                            :text="missingFilesMessage || 'All required model files must be uploaded before starting training.'"
                            variant="info"
                            :close="false"
                            :rounded="false"
                            :bordered="false"
                        />
                    </span>

                    <div class="flex flex-col h-full pt-4 overflow-y-auto grow">
                        <TrainingOptions :errors="errors" />
                    </div>
                </div>

                <div v-if="getStatus !== ModelStatusEnum.PENDING" class="flex flex-col h-full overflow-hidden">
                    <div class="flex flex-row w-full h-full max-h-[78vh] md:max-h-min border-t border-gray-200 dark:border-gray-700">
                        <div class="flex flex-col w-full overflow-y-auto divide-y divide-gray-100 dark:divide-gray-700 grow">
                            <div class="flex justify-end w-full p-2">
                                <div
                                    v-tippy="{ placement: 'left' }"
                                    class="p-2 transition bg-gray-100 border border-gray-300 rounded cursor-pointer dark:bg-gray-700 dark:border-gray-600 group"
                                    :content="showLogs ? 'Hide Logs' : 'Show logs'"
                                    @click="toggleLogs"
                                >
                                    <icon-heroicons-outline-chevron-double-right
                                        class="w-5 h-5 text-gray-400 dark:group-hover:text-gray-400 group-hover:text-gray-500 dark:text-gray-300"
                                        :class="[showLogs ? '' : 'rotate-180']"
                                    />
                                </div>
                            </div>
                            <div class="relative w-full h-full p-4 overflow-auto">
                                <TrainingMetrics :in-progress="!finished" />
                            </div>
                        </div>

                        <div v-if="showLogs" class="h-full border-l 2xl:w-96 bg-gray-50 dark:bg-gray-800 dark:border-l-gray-700 border-l-gray-300">
                            <Timeline data-test="training-timeline" :complete="finished ?? false" />
                        </div>
                    </div>
                </div>
            </div>
        </AiCard>
    </Form>
</template>

<script lang="ts" setup>
import { Form } from "vee-validate";
import { computed, ref } from "vue";
import { useRoute } from "vue-router";
import { array, lazy, object, string } from "yup";

import AiAlert from "@/components/AiAlert/AiAlert.vue";
import {
    IInitTraining, initialiseTraining,
    JobTypes,
    ModelStatus,
    ModelStatusEnum
} from "@/services/model-service";
import { Snackbar } from "@/utils/snackbar";

import Timeline from "./Timeline.vue";
import TrainingActionsMenu from "./TrainingActionsMenu.vue";
import TrainingMetrics from "./TrainingMetrics.vue";
import TrainingOptions from "./TrainingOptions.vue";

interface ITrainingProps {
    canTrain: boolean;
    status: ModelStatus;
    allFilesUploaded: boolean;
    requiredFiles: string[];
    uploadedFileNames: string[];
    jobType: JobTypes;
}

const props = defineProps<ITrainingProps>();

const emits = defineEmits(["started"]);

const route = useRoute();

const showLogs = ref(true);

/**
 * Computes the list of files that are still missing (required but not uploaded).
 */
const missingFiles = computed(() => {
    return props.requiredFiles.filter(f => !props.uploadedFileNames.includes(f));
});

/**
 * Generates a human-readable message about required and missing files.
 */
const missingFilesMessage = computed(() => {
    const requiredList = props.requiredFiles.map(f => `<code>${f}</code>`).join(", ");

    if (missingFiles.value.length === 0) {
        return "";
    }

    const missingList = missingFiles.value.map(f => `<code>${f}</code>`).join(", ");

    return `For job type <strong><code>${props.jobType}</code></strong>, required files are: ${requiredList}.<br/>Missing: ${missingList}`;
});

const schema = object().shape({
    enriched: string().required("Please confirm data enrichment."),
    trusts: lazy(trusts =>
        (Array.isArray(trusts)
            ?
            array()
                .of(string().required())
                .min(1, "You must select a minimum of one trust for training.")
                .required("You must select a minimum of one trust for training.")
            :
            string().required("You must select a minimum of one trust for training.")))
});

const formSubmitting = ref(false);

const getStatus = computed(() => {
    return ModelStatusEnum[props.status];
});

const finished = computed(() => {
    return [
        ModelStatusEnum.ERROR,
        ModelStatusEnum.RESULTS_UPLOADED,
        ModelStatusEnum.STOPPED
    ].includes(ModelStatusEnum[props.status]);
});

const initTraining = async (formData: unknown): Promise<void> => {
    if (formSubmitting.value) {
        return;
    }

    if (getStatus.value > ModelStatusEnum.INITIATED) {
        return;
    }

    formSubmitting.value = true;

    const { trusts } = formData as IInitTraining;

    // If it is only one trust, add to an array
    const arr: string[] = [];
    const requestData: IInitTraining = { trusts: arr.concat(trusts) };

    try {
        await initialiseTraining(
            route.params["modelId"].toString(),
            requestData
        );

        emits("started", true);

        formSubmitting.value = false;
    }
    catch {
        Snackbar.error({
            title: "There was an error starting training.",
            text: "We have been unable to start training for this model. Please try again later."
        });

        formSubmitting.value = false;
    }
};

const toggleLogs = () => {
    showLogs.value = !showLogs.value;
};

</script>
