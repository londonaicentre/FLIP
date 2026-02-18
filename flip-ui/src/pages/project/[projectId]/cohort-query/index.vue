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
<route lang="yaml">
    name: Cohort Query
</route>

<template>
    <div class="flex flex-col w-full h-full">
        <AiBreadcrumbs :pages="breadcrumbPages" :current="{name: 'Cohort Query'}" />

        <Transition name="fade">
            <div v-if="true" class="flex flex-col flex-1 min-w-0 overflow-hidden">
                <main class="flex flex-1 overflow-hidden">
                    <div class="flex flex-col flex-1 overflow-y-auto xl:overflow-hidden">
                        <div class="flex flex-1 xl:overflow-hidden">
                            <div class="p-4 mx-auto grow xl:overflow-y-auto">
                                <AiCard class="w-full space-y-2">
                                    <CohortQuery @update-project="updateProject" />
                                </AiCard>
                            </div>
                        </div>
                    </div>
                </main>
            </div>
        </Transition>
    </div>
</template>

<script lang="ts" setup>
import { storeToRefs } from "pinia";

import AiBreadcrumbs, { IPage } from "@/components/AiBreadcrumbs/AiBreadcrumbs.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import CohortQuery from "@/partials/cohort-query/CohortQuery.vue";
import { useProjectStore } from "@/store/project";

const projectStore = useProjectStore();
const { project } = storeToRefs(projectStore);

const breadcrumbPages: IPage[] = [
    {
        name: "Projects",
        path: "/projects"
    },
    {
        name: project?.value?.name ?? "Project",
        path: `/project/${project?.value?.id}`
    }
];

const emit = defineEmits(["UpdateProject"]);

const updateProject = () => {
    emit("UpdateProject");
};
</script>
