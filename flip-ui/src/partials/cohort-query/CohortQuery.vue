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
    <div>
        <Transition name="fade" mode="out-in">
            <AiLoader v-if="!project" />
            <Form v-else :validation-schema="schema" class="flex flex-col w-full overflow-y-auto" @submit="runCohortQuery">
                <AiAlert
                    :variant="'info'"
                    :text="queryLocked
                        ? 'This query is now locked and can not be edited as the project has been staged.'
                        : 'This query will be sent to all participating Trusts and the time taken can vary depending on the query.'
                    "
                    :bordered="false"
                    :rounded="false"
                />
                <div class="relative p-4 transition">
                    <div
                        class="relative space-y-4"
                    >
                        <div class="relative h-full">
                            <div class="space-y-4">
                                <AiCodeTextArea
                                    :initial-value="project?.query?.query"
                                    :input-props="{readonly: queryLocked}"
                                    name="query"
                                    label=""
                                    data-test="cohort-query"
                                />
                                <div v-if="!queryLocked">
                                    <AiButton
                                        primary
                                        :loading="formSubmitting"
                                        :disabled="formSubmitting"
                                        data-test="view-cohort-query-results-btn"
                                        class="mb-1"
                                        type="submit"
                                    >
                                        Run & Save Query
                                    </AiButton>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                <div v-if="queryId && !project?.query" class="flex items-center gap-2 px-4 py-3 text-sm text-blue-700 dark:text-blue-300">
                    <icon-heroicons-outline-clock class="w-5 h-5" />
                    Awaiting trust results…
                </div>
                <div v-if="project?.query" class="relative p-4 pt-4 space-y-4 bg-gray-200 dark:bg-gray-600">
                    <Transition name="slidedown">
                        <div v-if="true" class="overflow-hidden border border-gray-300 rounded-lg shadow-lg dark:border-gray-700 bg-gray-50 dark:bg-gray-900">
                            <QueryResultCharts />
                        </div>
                    </Transition>
                </div>
            </Form>
        </Transition>
    </div>
</template>

<script setup lang="ts">
import { Form } from "vee-validate";
import { computed, onBeforeMount, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { object, string } from "yup";

import AiAlert from "@/components/AiAlert/AiAlert.vue";
import AiButton from "@/components/AiButton/AiButton.vue";
import AiCodeTextArea from "@/components/AiTextArea/AiCodeTextArea.vue";
import router from "@/router";
import { ICohortQueryCreate, sendQuery } from "@/services/cohort-query-service";
import { IProject } from "@/services/project-service";
import { useProjectStore } from "@/store/project";
import { containsForbiddenCommands } from "@/utils/cohort/query";
import { Snackbar } from "@/utils/snackbar";

import QueryResultCharts from "./QueryResultCharts.vue";

const route = useRoute();
const projectStore = useProjectStore();

const queryId = ref<string>("");
const project = ref<IProject>();
const formSubmitting = ref<boolean>(false);

const emits = defineEmits(["UpdateProject"]);

onBeforeMount(() => {
    project.value = projectStore.project;
});

watch(projectStore, () => project.value = projectStore.project);

const schema = object().shape({
    query: string()
        .trim()
        .required("A query is required and can't be left blank")
        .test(
            "valid-query",
            "Please enter a valid query",
            function() {
                try {
                    return !containsForbiddenCommands(this.parent.query);
                } catch {
                    return false;
                }
            })
});

const runCohortQuery = async (v: unknown) => {

    if(formSubmitting.value || queryLocked.value) {
        return;
    }

    formSubmitting.value = true;

    const values = v as ICohortQueryCreate;

    try {
        const response = await sendQuery("/step/cohort", {
            ...values,
            name: `${project.value?.name}: Cohort Query`,
            projectId: route.params["projectId"].toString()
        });

        if (response && response.trust.every(r => r.statusCode >= 200 && r.statusCode < 300)) {
            queryId.value = response.queryId;

            hasResults();

            Snackbar.show({
                type: "success",
                text: "Cohort query has been sent to trusts and queued for processing",
                title: "Cohort Query Sent",
                actionText: "View Project",
                action: () => router.push({ path: `/project/${project.value?.id}` })
            });

            formSubmitting.value = false;

            return;
        }

        const message = response.trust.map(
            r => `Trust: ${r.name} (Error ${r.statusCode}): ${r.message}`
        ).join("\n\n");

        throw new Error(message);

    }
    catch(e) {
        Snackbar.error({
            title: "Error running cohort query",
            text: "There was a problem running this cohort query:\n\n " + (e as Error).message,
        });

        formSubmitting.value = false;

        return;
    }
};

const hasResults = () => {
    formSubmitting.value = false;
    emits("UpdateProject");
};

const queryLocked = computed(() => project?.value?.status !== "UNSTAGED");
</script>
