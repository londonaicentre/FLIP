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
                Complete the following fields to initiate training.
            </p>
        </div>
        <div class="mt-6 space-y-6 divide-y sm:mt-5 sm:space-y-5 divide dark:divide-dark-border">
            <div class="sm:grid sm:grid-cols-3 sm:gap-4 sm:items-center sm:pt-5">
                <label for="enriched" class="block text-sm font-medium text-gray-700 dark:text-gray-300 sm:mt-px sm:pt-2">
                    Dataset enriched
                    <div class="mr-2 text-sm text-gray-400 dark:text-gray-300">
                        Confirm your dataset has been enriched as required before training
                    </div>
                </label>
                <div class="mt-1 text-right sm:mt-0 sm:col-span-2">
                    <AiSwitch
                        name="enriched"
                        value="true"
                        data-test="data-enrichment-btn"
                        :disabled="disabled"
                        :label="{ enabled: 'Dataset Enriched', disabled: 'Dataset Not Enriched' }"
                    />
                </div>
            </div>
            <div class="sm:grid sm:grid-cols-3 sm:gap-4 sm:items-start sm:pt-5">
                <label class="block space-y-2 text-sm font-medium text-gray-700 dark:text-gray-300 sm:mt-px sm:pt-2">
                    Participating trusts
                    <div class="mr-2 text-sm text-gray-400 dark:text-gray-300">
                        A minimum of 1 trust must be selected for training
                    </div>
                </label>
                <div class="sm:col-span-2">
                    <div class="flow-root">
                        <div>
                            <dl class="divide-y divide-gray-200 dark:divide-dark-border">
                                <div
                                    v-for="(trust, i) in trustsToSelect"
                                    :key="trust.trustId"
                                    class="flex items-center justify-between py-2 text-sm font-medium"
                                >
                                    <dt class="flex items-center font-semibold text-gray-500 dark:text-primary-200">
                                        <div class="flex items-center">
                                            <span class="px-2 py-1">
                                                {{ trust.trustName }}
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
                                            :label="{ enabled: 'Trust Included', disabled: 'Trust Excluded' }"
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
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed, ComputedRef } from "vue";

import AiSwitch from "@/components/AiSwitch/AiSwitch.vue";
import { useProjectStore } from "@/store/project";

interface ITrainingOptionsProps {
    errors: Record<string, string | undefined>
    // A dispatched run's configuration is a record, not a form: the controls stay
    // on screen so you can see which trusts took part, but nothing is editable.
    disabled?: boolean
}

interface ITrustsToTrain {
    // Display label only — names are admin-chosen and non-unique.
    trustName: string;
    // Stable identity sent to the training-init endpoint.
    trustId: string;
}

withDefaults(defineProps<ITrainingOptionsProps>(), { disabled: false });

const projectStore = useProjectStore();

const approvedTrusts = projectStore.project?.approvedTrusts;

const trustsToSelect: ComputedRef<ITrustsToTrain[] | undefined> = computed(() =>
    approvedTrusts?.filter(t => t.approved)
        .map(t =>
            ({
                trustName: t.name,
                trustId: t.id
            })
        )
        .sort((a, b) => a.trustName.localeCompare(b.trustName))
);
</script>
