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
    <section id="cohort-query-section">
        <AiCard>
            <div v-if="!queryDetails" data-test="empty-cohort-query">
                <div class="p-4">
                    <h2 class="text-lg font-semibold font-heading leading-loose">
                        Cohort Query
                    </h2>
                    <div
                        class="flex flex-col items-center justify-center w-full h-full gap-4 p-4 mt-4 border-2 border-gray-300 dark:border-gray-700 border-dashed rounded-lg"
                    >
                        <div class="relative block w-full text-center">
                            <icon-ph-chart-bar-duotone class="w-12 h-12 mx-auto text-primary-500 dark:text-primary-400" />
                            <div class="mt-2 text-xs leading-snug">
                                There is no cohort query assigned to this project.
                            </div>
                        </div>
                        <AiButton v-if="!isObserver" primary data-test="create-query-btn" @click="addCohortQuery">
                            Create Cohort Query
                        </AiButton>
                    </div>
                </div>
            </div>
            <div v-else data-test="cohort-query-exists">
                <div class="flex items-center justify-between p-4">
                    <h2 class="text-lg font-semibold font-heading leading-loose">
                        Cohort Query
                    </h2>
                    <AiButton light data-test="view-results-btn" @click="viewCohortQueryResults">
                        View Query
                    </AiButton>
                </div>
                <div class="flow-root">
                    <div>
                        <dl class="border-t border-b border-gray-200 divide-y divide-gray-200 dark:divide-gray-700 dark:border-gray-700">
                            <div class="flex justify-between px-4 py-3 text-xs font-medium">
                                <dt class="text-gray-500 dark:text-gray-300">
                                    Estimated Cohort Size
                                </dt>
                                <dd class="pl-8 font-semibold truncate dark:text-gray-400" data-test="model-dashboard-cohort-size">
                                    {{ totalCount }}
                                </dd>
                            </div>
                            <div class="flex justify-between px-4 py-3 text-xs font-medium">
                                <dt class="text-gray-500 dark:text-gray-300">
                                    Trusts Queried
                                </dt>
                                <dd class="pl-8 font-semibold truncate dark:text-gray-400" data-test="model-dashboard-cohort-trusts-queried">
                                    {{ queryDetails?.queriedTrustIds?.length ?? 0 }}
                                </dd>
                            </div>
                        </dl>
                    </div>
                </div>
            </div>
        </AiCard>
    </section>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useRouter } from "vue-router";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import { usePermissions } from "@/composables/usePermissions";
import { IProjectQuery } from "@/services/project-service";
import { useProjectStore } from "@/store/project";


interface IQueryDetails {
    queryDetails: IProjectQuery | undefined;
}

const router = useRouter();
const project = useProjectStore().project;

const { isObserver } = usePermissions();

const props = defineProps<IQueryDetails>();

const totalCount = computed(() => {
    return props.queryDetails?.totalCohort.toLocaleString() ?? "Unknown";
});

const viewCohortQueryResults = () => {
    router.push({ path: `/project/${project?.id}/cohort-query` });
};

const addCohortQuery = () => {
    router.push({ path: `/project/${project?.id}/cohort-query` });
};

</script>
