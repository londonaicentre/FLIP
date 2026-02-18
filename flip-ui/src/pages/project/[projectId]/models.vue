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
    name: Project Models
</route>

<template>
    <div class="flex flex-col h-full overflow-hidden">
        <AiBreadcrumbs :pages="breadcrumbPages" :current="{name: 'Models'}" />
        <div class="h-full p-4 overflow-y-auto">
            <AiCard class="h-full p-4 overflow-y-auto grow">
                <ModelList />
            </AiCard>
        </div>
    </div>
</template>

<script setup lang="ts">
import { onBeforeMount } from "vue";

import AiBreadcrumbs, { IPage } from "@/components/AiBreadcrumbs/AiBreadcrumbs.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import ModelList from "@/partials/models/ModelList.vue";
import { routeChange } from "@/router";
import { useProjectStore } from "@/store/project";
import { Snackbar } from "@/utils/snackbar";

const projectStore = useProjectStore();

onBeforeMount(() => {
    if(!projectStore.isApproved) {
        Snackbar.error({
            title: "Requires Project Approval",
            text: "Unable to view models as this project is not yet approved."
        });

        routeChange.viewProject(projectStore.getProject?.id ?? "");
    }
});

const breadcrumbPages: IPage[] = [
    {
        name: "Projects",
        path: "/projects"
    },
    {
        name: projectStore.project?.name ?? "",
        path: `/project/${projectStore.project?.id ?? ""}`
    }
];
</script>
