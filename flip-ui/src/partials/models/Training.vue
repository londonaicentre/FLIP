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
    <Form
        v-if="view === 'prepare' && getStatus === ModelStatusEnum.PENDING"
        ref="formRef"
        v-slot="{ errors }"
        class="flex flex-col w-full h-full"
        :validation-schema="schema"
        @submit="initTraining"
    >
        <AiCard class="flex flex-col flex-1 min-h-0 overflow-hidden">
            <template v-if="!allFilesUploaded">
                <AiAlert
                    variant="info"
                    :close="false"
                    :rounded="false"
                    :bordered="false"
                >
                    <template v-if="missingFiles.length">
                        For <template v-if="flBackendLabel">
                            <strong><code>{{ flBackendLabel }}</code></strong> and
                        </template>job type <strong><code>{{ jobType }}</code></strong>, required files are:
                        <template v-for="(f, i) in requiredFiles" :key="f">
                            <code>{{ f }}</code><template v-if="i < requiredFiles.length - 1">
                                ,
                            </template>
                        </template>.
                    </template>
                    <template v-else>
                        All required model files must be uploaded before starting training.
                    </template>
                </AiAlert>
                <AiAlert
                    v-if="missingFiles.length"
                    variant="warning"
                    :close="false"
                    :rounded="false"
                    :bordered="false"
                >
                    Missing:
                    <template v-for="(f, i) in missingFiles" :key="f">
                        <code>{{ f }}</code><template v-if="i < missingFiles.length - 1">
                            ,
                        </template>
                    </template>
                </AiAlert>
            </template>

            <div class="flex flex-col flex-1 pt-4 overflow-y-auto">
                <TrainingOptions :errors="errors" />
            </div>
        </AiCard>
    </Form>

    <div v-else-if="view === 'run'" class="flex flex-col xl:flex-row flex-1 min-h-0 gap-4 xl:items-start">
        <AiCard class="flex flex-col flex-1 min-w-0 p-4">
            <TrainingMetrics :in-progress="!finished" />
        </AiCard>

        <AiCard class="relative w-full xl:w-96 2xl:w-[28rem] xl:shrink-0 self-stretch flex flex-col py-4 pl-4 pr-1 xl:p-0">
            <!-- At xl the content column is absolutely positioned so the timeline's
                     length can't inflate the card past its siblings: the metrics card
                     defines the row height and self-stretch matches it exactly. -->
            <div class="flex flex-col flex-1 min-h-0 xl:absolute xl:inset-0 xl:py-4 xl:pl-4 xl:pr-1">
                <div class="flex items-center gap-2 shrink-0 mb-3">
                    <span class="relative flex items-center justify-center w-2 h-2">
                        <span
                            v-if="!finished"
                            data-test="live-activity-ping"
                            class="absolute inline-flex w-full h-full rounded-full opacity-60 bg-primary-500 animate-ping"
                        />
                        <span
                            data-test="live-activity-dot"
                            class="relative inline-flex w-2 h-2 rounded-full"
                            :class="liveActivityDotClass"
                        />
                    </span>
                    <h2 class="text-base font-heading font-semibold text-gray-900 dark:text-gray-100">
                        Live activity
                    </h2>
                </div>
                <div class="flex-1 min-h-0 overflow-y-auto overflow-x-hidden">
                    <Timeline data-test="training-timeline" :complete="finished ?? false" />
                </div>
            </div>
        </AiCard>
    </div>
</template>

<script lang="ts" setup>
import { Form } from "vee-validate";
import { computed, ref } from "vue";
import { useRoute } from "vue-router";
import { array, lazy, object, string } from "yup";

import AiAlert from "@/components/AiAlert/AiAlert.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import { getStatusEnumValue, IInitTraining, initialiseTraining,
    JobType,
    ModelStatus,
    ModelStatusEnum } from "@/services/model-service";
import { Snackbar } from "@/utils/snackbar";

import Timeline from "./Timeline.vue";
import TrainingMetrics from "./TrainingMetrics.vue";
import TrainingOptions from "./TrainingOptions.vue";

interface ITrainingProps {
    canTrain: boolean;
    status: ModelStatus;
    allFilesUploaded: boolean;
    requiredFiles: string[];
    uploadedFileNames: string[];
    jobType: JobType;
    flBackendLabel?: string;
    // Which stage tab is showing: "prepare" owns the run-options form (and renders
    // nothing once the model is dispatched — a dispatched run has no options left to
    // set); "run" owns the metrics and the activity feed.
    view: "prepare" | "run";
}

const props = defineProps<ITrainingProps>();

const emits = defineEmits(["started"]);

const route = useRoute();

/**
 * Computes the list of files that are still missing (required but not uploaded).
 */
const missingFiles = computed(() => {
    return props.requiredFiles.filter(f => !props.uploadedFileNames.includes(f));
});

const schema = object().shape({
    enriched: string().required("Please confirm data enrichment."),
    trust_ids: lazy(trustIds =>
        (Array.isArray(trustIds)
            ?
            array()
                .of(string().required())
                .min(1, "You must select a minimum of one trust for training.")
                .required("You must select a minimum of one trust for training.")
            :
            string().required("You must select a minimum of one trust for training.")))
});

const formSubmitting = ref(false);

// Form is owned by Training so the vee-validate context wraps TrainingOptions's
// fields, but the submit trigger lives in the page header. Page calls
// initiateTraining() via a ref to fire the native form submit, which lets
// vee-validate validate and route into the existing @submit handler.
const formRef = ref<{ $el?: HTMLFormElement } | null>(null);

defineExpose({
    initiateTraining() {
        const el = formRef.value?.$el;
        if (el && typeof el.requestSubmit === "function") {
            el.requestSubmit();
        }
    },
    isSubmitting: formSubmitting
});

const getStatus = computed(() => {
    return getStatusEnumValue(props.status);
});

const finished = computed(() => {
    return [
        ModelStatusEnum.ERROR,
        ModelStatusEnum.RESULTS_UPLOADED,
        ModelStatusEnum.RESULTS_UPLOAD_FAILED,
        ModelStatusEnum.STOPPED
    ].includes(getStatus.value);
});

const isError = computed(() => getStatus.value === ModelStatusEnum.ERROR);

const liveActivityDotClass = computed(() => {
    if (isError.value) return "bg-red-600";
    if (finished.value) return "bg-gray-400";

    return "bg-primary-600";
});

const initTraining = async (formData: unknown): Promise<void> => {
    if (formSubmitting.value) {
        return;
    }

    if (getStatus.value > ModelStatusEnum.INITIATED) {
        return;
    }

    formSubmitting.value = true;

    const { trust_ids } = formData as IInitTraining;

    // If it is only one trust, add to an array
    const arr: string[] = [];
    const requestData: IInitTraining = { trust_ids: arr.concat(trust_ids) };

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

</script>
