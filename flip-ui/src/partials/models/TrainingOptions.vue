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
    <div class="px-6 pb-4 space-y-8 divide-y divide-gray-200 dark:divide-dark-border sm:space-y-5">
        <div>
            <p class="max-w-2xl mt-1 text-sm text-gray-500 dark:text-gray-300">
                Complete the following to initiate training.
            </p>
        </div>
        <div class="mt-6 space-y-6 divide-y sm:mt-5 sm:space-y-5 divide dark:divide-dark-border">
            <div
                v-if="requiredFiles.length"
                data-test="required-files-row"
                class="sm:grid sm:grid-cols-3 sm:gap-4 sm:items-center sm:pt-5"
            >
                <div class="block text-sm font-medium text-gray-700 dark:text-gray-300 sm:col-span-2 sm:mt-px sm:pt-2">
                    Required files
                    <div
                        data-test="required-files-summary"
                        class="mr-2 text-sm"
                        :class="requiredReady ? 'text-gray-400 dark:text-gray-300' : 'text-amber-800 dark:text-amber-300'"
                    >
                        {{ requiredSummary }}
                    </div>
                </div>
                <div class="mt-1 text-right sm:mt-0 sm:col-span-1">
                    <span
                        data-test="required-files-status"
                        class="inline-flex px-2.5 py-1 text-[13px] font-semibold rounded-full"
                        :class="requiredReady
                            ? 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300'
                            : 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300'"
                    >
                        {{ requiredPill }}
                    </span>
                </div>
            </div>
            <div class="sm:grid sm:grid-cols-3 sm:gap-4 sm:items-center sm:pt-5">
                <label for="enriched" class="block text-sm font-medium text-gray-700 dark:text-gray-300 sm:col-span-2 sm:mt-px sm:pt-2">
                    Dataset enriched
                    <div class="mr-2 text-sm text-gray-400 dark:text-gray-300">
                        {{ hasImaging
                            ? "Confirm your dataset has been enriched as required before training"
                            : "This project has no imaging to enrich. Confirm your data is ready before training" }}
                    </div>
                </label>
                <div class="mt-1 text-right sm:mt-0 sm:col-span-1">
                    <AiSwitch
                        name="enriched"
                        value="true"
                        data-test="data-enrichment-btn"
                        :disabled="disabled"
                        :label="{ enabled: 'Dataset Enriched', disabled: 'Dataset Not Enriched' }"
                    />
                </div>
            </div>
            <!-- Fifths rather than thirds, so the hint fits on one line beside the Trust list. -->
            <div class="sm:grid sm:grid-cols-5 sm:gap-4 sm:items-start sm:pt-5">
                <label class="block space-y-2 text-sm font-medium text-gray-700 dark:text-gray-300 sm:col-span-2 sm:mt-px sm:pt-2">
                    Participating Trusts
                    <div class="mr-2 text-sm text-gray-400 dark:text-gray-300">
                        A minimum of 1 Trust must be selected for training
                    </div>
                </label>
                <div class="sm:col-span-3">
                    <div class="flow-root">
                        <div>
                            <dl class="divide-y divide-gray-200 dark:divide-dark-border">
                                <div
                                    v-for="(trust, i) in trustsToSelect"
                                    :key="trust.trustId"
                                    class="flex items-center justify-end gap-4 py-2 text-sm font-medium"
                                >
                                    <dt class="flex items-center font-semibold text-gray-500 dark:text-primary-200">
                                        <div class="flex items-center">
                                            <span class="px-2 py-1">
                                                {{ trust.trustLabel }}
                                            </span>
                                        </div>
                                    </dt>
                                    <dd class="font-semibold">
                                        <AiSwitch
                                            name="trust_ids"
                                            :data-test="`trust-selection-${i}`"
                                            :value="trust.trustId"
                                            :disabled="disabled"
                                            hide-error
                                            :aria-label="trust.trustLabel"
                                        />
                                    </dd>
                                </div>
                                <div
                                    v-if="errors.trust_ids"
                                    class="py-2 text-sm text-right text-red-600 dark:text-red-400"
                                >
                                    {{ errors.trust_ids }}
                                </div>
                            </dl>
                        </div>
                    </div>
                </div>
            </div>
            <div class="sm:grid sm:grid-cols-3 sm:gap-4 sm:items-start sm:pt-5">
                <label for="gpu_override" class="block text-sm font-medium text-gray-700 dark:text-gray-300 sm:col-span-2 sm:mt-px sm:pt-2">
                    GPUs per trust
                    <div class="mr-2 text-sm text-gray-400 dark:text-gray-300">
                        Off, the GPUs in the job's <code>meta.json</code> apply, or the platform default. On, this run
                        asks every selected trust for the number you set here. A trust that cannot provide it holds
                        the run until it can; the request is never lowered to fit.
                        <template v-if="isFlower">
                            Flower does not schedule jobs by GPU, so on this net the request is recorded, not
                            enforced.
                        </template>
                    </div>
                </label>
                <div class="mt-1 space-y-3 sm:mt-0 sm:col-span-1">
                    <div class="text-right">
                        <AiSwitch
                            name="gpu_override"
                            value="true"
                            data-test="gpu-override-switch"
                            :disabled="disabled"
                            :label="{ enabled: 'Override for this run', disabled: 'Job default' }"
                        />
                    </div>
                    <div v-if="overrideOn" class="w-28 ml-auto">
                        <AiInput
                            name="num_gpus"
                            type="number"
                            label="GPUs"
                            data-test="gpu-count-input"
                            :initial-value="runResources ? String(runResources.num_gpus) : ''"
                            :input-props="{ min: 0, max: MAX_GPUS_PER_SITE, step: 1, disabled }"
                        />
                    </div>
                </div>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { useFieldValue } from "vee-validate";
import { computed, ComputedRef } from "vue";

import AiInput from "@/components/AiInput/AiInput.vue";
import AiSwitch from "@/components/AiSwitch/AiSwitch.vue";
import { FileUploadStatus, IRequiredFile } from "@/interfaces/model/types";
import { projectHasImaging } from "@/partials/projects/projectType";
import { IJobResources, MAX_GPUS_PER_SITE } from "@/services/model-service";
import { useProjectStore } from "@/store/project";

interface ITrainingOptionsProps {
    errors: Record<string, string | undefined>
    // A dispatched run's configuration is a record, not a form: the controls stay
    // on screen so you can see which trusts took part, but nothing is editable.
    disabled?: boolean
    // "FLARE" or "Flower", when known: Flower records a GPU request without enforcing it.
    flBackendLabel?: string
    // The files this model's backend + job type require, with their upload status. Empty while the
    // job types are unknown, which hides the row.
    requiredFiles?: IRequiredFile[]
    // A dispatched run's GPU override. AiInput seeds its own field and would overwrite the Form's
    // initialValues with "", so the inputs take the run's values directly.
    runResources?: IJobResources | null
}

interface ITrustsToTrain {
    // Sort key. Names are admin-chosen and non-unique, so they order the list
    // but never identify a trust on their own.
    trustName: string;
    // What the user reads: the name with its code appended to disambiguate two
    // trusts sharing a name. Falls back to the bare name when there is no code.
    trustLabel: string;
    // Stable identity sent to the training-init endpoint.
    trustId: string;
}

const props = withDefaults(defineProps<ITrainingOptionsProps>(), {
    disabled: false,
    flBackendLabel: undefined,
    requiredFiles: () => [],
    runResources: null
});

// The same states the file list shows, in the order they need attention. A missing file has no status.
const REQUIRED_FILE_GROUPS: [string, FileUploadStatus | undefined][] = [
    ["Missing", undefined],
    ["Unsafe content", FileUploadStatus.INFECTED],
    ["Upload failed", FileUploadStatus.ERROR],
    ["Uploading", FileUploadStatus.UPLOADING],
    ["Checking", FileUploadStatus.SCANNING]
];
const IN_FLIGHT_STATUSES = [FileUploadStatus.UPLOADING, FileUploadStatus.SCANNING];

const readyCount = computed(() =>
    props.requiredFiles.filter(f => f.status === FileUploadStatus.COMPLETED).length);
const requiredReady = computed(() => readyCount.value === props.requiredFiles.length);

const requiredSummary = computed(() => (requiredReady.value
    ? "All required files uploaded"
    : REQUIRED_FILE_GROUPS.flatMap(([label, status]) => {
        const names = props.requiredFiles.filter(f => f.status === status).map(f => f.name);

        return names.length ? [`${label} ${names.join(", ")}`] : [];
    }).join(" · ")));

// "Checking" when nothing is left to do but wait for uploads and checks to finish.
const requiredPill = computed(() => {
    if (requiredReady.value) return "Complete";
    const waiting = props.requiredFiles.every(f =>
        f.status === FileUploadStatus.COMPLETED || (f.status && IN_FLIGHT_STATUSES.includes(f.status)));

    return waiting ? "Checking" : `${readyCount.value} of ${props.requiredFiles.length}`;
});

// The GPU inputs appear only once the researcher chooses to override the job's own request (FLIP#70).
const overrideValue = useFieldValue<string | undefined>("gpu_override");
const overrideOn = computed(() => Boolean(overrideValue.value));
const isFlower = computed(() => props.flBackendLabel === "Flower");

const projectStore = useProjectStore();

const approvedTrusts = projectStore.project?.approvedTrusts;

// The enrichment attestation stays for every project; only its wording depends on whether there
// is imaging to enrich (FLIP#1071). Absent on a hub predating the flag, which means imaging.
const hasImaging = computed(() => projectHasImaging(projectStore.project));

const trustsToSelect: ComputedRef<ITrustsToTrain[] | undefined> = computed(() =>
    approvedTrusts?.filter(t => t.status === "APPROVED")
        .map(t =>
            ({
                trustName: t.name,
                trustLabel: t.code ? `${t.name} (${t.code})` : t.name,
                trustId: t.id
            })
        )
        .sort((a, b) => a.trustName.localeCompare(b.trustName))
);
</script>
