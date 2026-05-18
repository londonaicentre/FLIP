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
    <AiCard>
        <div class="p-4 space-y-4">
            <h2 class="text-lg font-semibold font-heading grow leading-loose">
                Project Staging
            </h2>
        </div>
        <Form
            v-if="trustsToStage?.length"
            v-slot="{errors}"
            :validation-schema="schema"
            :initial-values="{ trusts: [] }"
            @submit="stageProject"
        >
            <div v-if="hasQuery" class="w-full gap-3 text-sm">
                <ul role="list" class="border-gray-200 divide-y divide-gray-200 border-y dark:border-gray-700 dark:divide-gray-700">
                    <li v-for="trust in trustsToStage" :key="trust.id">
                        <div class="flex items-center py-4 transition hover:bg-gray-50 dark:hover:bg-gray-800 group">
                            <div class="flex items-center flex-1 px-4 grow">
                                <div class="flex-1 min-w-0">
                                    <div>
                                        <p
                                            class="text-sm font-semibold truncate text-primary-600 dark:text-primary-200"
                                            :title="trust.name"
                                        >
                                            {{ trust.code || trust.name }}
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
                            v-if="!isObserver"
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
import { usePermissions } from "@/composables/usePermissions";
import { ITrustResponse } from "@/services/trust-service";
import { useTrustStore } from "@/store/trusts";

interface IProjectStagingProps {
    staging: boolean;
    hasQuery: boolean;
    // Trust IDs that participated in the project's cohort query. The staging
    // selector is intersected with this list so trusts that joined the
    // platform *after* the query was submitted aren't offered — we have no
    // cohort count for them, so staging against them would be blind.
    queriedTrustIds?: string[];
}

interface IFormValue {
    trusts: string[];
}

interface ITrustToStage extends ITrustResponse {
    staged: boolean;
}

const props = defineProps<IProjectStagingProps>();

const emits = defineEmits(["staged"]);

const { isObserver } = usePermissions();

const loadingTrusts = ref<boolean>(true);
const trustsToStage = ref<ITrustToStage[]>();
const trustStore = useTrustStore();

watch([trustStore, () => props.queriedTrustIds], () => {
    const trusts = Array.isArray(trustStore.getTrusts) ? trustStore.getTrusts : [];
    const queriedTrustIds = props.queriedTrustIds;
    // When the parent hasn't loaded the project yet, `queriedTrustIds` is
    // undefined — fall through to the unfiltered list so the loader/empty
    // state still renders correctly. Once the project lands, an empty array
    // means "the query returned no trusts", which legitimately hides every
    // toggle.
    const filtered = queriedTrustIds === undefined
        ? trusts
        : trusts.filter((trust) => queriedTrustIds.includes(trust.id));

    trustsToStage.value = filtered
        .map((trust) => ({
            ...trust,
            staged: false
        }))
        .sort((a, b) => (a.code || a.name).localeCompare(b.code || b.name));

    loadingTrusts.value = false;
}, { immediate: true });

const schema = object().shape({
    // vee-validate collects same-named checkboxes into an array, but until two
    // are checked it may hand a bare string through. Coerce to an array first
    // so the .of/.min checks always see the right shape.
    trusts: array()
        .transform((value) => (typeof value === "string" ? [value] : value))
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
