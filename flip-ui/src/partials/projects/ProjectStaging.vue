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
    <AiCard>
        <div class="p-4 space-y-4">
            <h2 class="text-lg font-semibold font-heading grow leading-loose">
                Project Staging
            </h2>
        </div>
        <Form v-if="trustsToStage?.length" v-slot="{errors}" :validation-schema="schema" @submit="stageProject">
            <div v-if="hasQuery" class="w-full gap-3 text-sm">
                <ul role="list" class="border-gray-200 divide-y divide-gray-200 border-y dark:border-gray-700 dark:divide-gray-700">
                    <li v-for="trust in trustsToStage" :key="trust.id">
                        <div class="flex items-center py-4 transition hover:bg-gray-50 dark:hover:bg-gray-800 group">
                            <div class="flex items-center flex-1 px-4 grow">
                                <div class="flex-1 min-w-0">
                                    <div>
                                        <p class="text-sm font-semibold truncate text-primary-600 dark:text-primary-200">
                                            {{ trust.name }}
                                        </p>
                                    </div>
                                </div>
                            </div>
                            <div class="px-4">
                                <AiSwitch
                                    name="trusts"
                                    :data-test="`${trust.name}-selector`"
                                    :value="trust.id"
                                    :disabled="staging"
                                    hide-error
                                    :label="{ enabled: 'Trust Included', disabled: 'Trust Excluded' }"
                                />
                            </div>
                        </div>
                    </li>
                    <div v-if="errors.trusts" class="px-4 py-2 text-sm text-right text-red-600 dark:text-red-400">
                        {{ errors.trusts }}
                    </div>
                </ul>
                <div class="p-4">
                    <div class="inline-flex justify-end w-full space-x-4">
                        <AiButton
                            class="ml-2"
                            primary
                            small
                            data-test="stage-project-btn"
                            :disabled="staging"
                            :loading="staging"
                            type="submit"
                        >
                            Stage Project
                        </AiButton>
                    </div>
                </div>
            </div>
            <template v-if="!hasQuery">
                <AiAlert
                    text="A cohort query is required before staging a project"
                    variant="info"
                    class="m-auto text-base"
                    :rounded="false"
                    :bordered="false"
                    :close="true"
                />
                <div
                    v-if="!hasQuery"
                    class="relative flex flex-col items-center justify-center"
                    data-test="models-unapproved-status"
                >
                    <div class="flex w-full p-4 grow">
                        <div class="w-full space-y-2">
                            <AiSkeleton v-for="i in 2" :key="i" class="h-8 animate-none" />
                        </div>
                    </div>
                    <div>
                        <div class="absolute inset-0 top-0 w-full h-full backdrop-blur-sm" />
                    </div>
                </div>
            </template>
        </Form>
        <AiLoader v-if="loadingTrusts" class="pb-4" />
        <div v-else-if="!trustsToStage?.length">
            <AiAlert
                :rounded="false"
                variant="error"
                text="Unable to load Trusts, please try again. If the issue persists please contact the service desk."
                :bordered="false"
            />
        </div>
    </AiCard>
</template>

<script setup lang="ts">
import { Form } from "vee-validate";
import { ref, watch } from "vue";
import { array, object, string } from "yup";

import AiAlert from "@/components/AiAlert/AiAlert.vue";
import AiButton from "@/components/AiButton/AiButton.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import { ITrustResponse } from "@/services/trust-service";
import { useTrustStore } from "@/store/trusts";

interface IProjectStagingProps {
    staging: boolean;
    hasQuery: boolean;
}

interface IFormValue {
    trusts: string[];
}

interface ITrustToStage extends ITrustResponse {
    staged: boolean;
}

defineProps<IProjectStagingProps>();

const emits = defineEmits(["staged"]);

const loadingTrusts = ref<boolean>(true);
const trustsToStage = ref<ITrustToStage[]>();
const trustStore = useTrustStore();

watch(trustStore, () => {
    trustsToStage.value = trustStore.getTrusts.map((trust) => ({
        ...trust,
        staged: false
    }));

    loadingTrusts.value = false;
}, { immediate: true });

const schema = object().shape({
    trusts: array()
        .of(string().required())
        .min(1, "You must select a minimum of one trust when staging.")
        .required("You must select a minimum of one trust when staging.")
});

const stageProject = (v: unknown) => {

    const formValue = v as IFormValue;

    if (formValue.trusts.length > 0 ?? false) {
        emits("staged", formValue.trusts);
    }
};
</script>
