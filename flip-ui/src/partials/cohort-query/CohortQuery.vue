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
            <Form
                v-else
                id="cohort-query-form"
                :validation-schema="schema"
                class="flex flex-col w-full overflow-y-auto"
                @submit="runCohortQuery"
            >
                <div class="relative p-4 transition">
                    <div class="grid grid-cols-1 gap-4 lg:grid-cols-3">
                        <div class="space-y-2 lg:col-span-2">
                            <div
                                v-if="lastRunLine"
                                class="text-xs font-semibold tracking-wider uppercase font-mono text-gray-500 dark:text-gray-400"
                            >
                                {{ lastRunLine }}
                            </div>
                            <AiCodeTextArea
                                :initial-value="project?.query?.query"
                                :input-props="{readonly: queryLocked || isViewer}"
                                :height="440"
                                name="query"
                                label=""
                                data-test="cohort-query"
                            />
                        </div>
                        <div class="flex flex-col gap-3 min-h-[440px]">
                            <CohortAggregateCard :submitting="formSubmitting" />
                            <div class="flex-1 min-h-0">
                                <PerTrustResponse :submitting="formSubmitting" />
                            </div>
                        </div>
                    </div>
                </div>
                <div v-if="queryId && !project?.query" class="flex items-center gap-2 px-4 py-3 text-sm text-blue-700 dark:text-blue-300">
                    <icon-heroicons-outline-clock class="w-5 h-5" />
                    Awaiting trust results…
                </div>
                <div v-if="project?.query" class="relative p-4 pt-4 space-y-4">
                    <Transition name="slidedown">
                        <div v-if="true" class="overflow-hidden border border-gray-300 rounded-lg shadow-lg dark:border-gray-700 bg-gray-50 dark:bg-gray-900">
                            <QueryResultCharts :submitting="formSubmitting" />
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

import AiCodeTextArea from "@/components/AiTextArea/AiCodeTextArea.vue";
import { usePermissions } from "@/composables/usePermissions";
import router from "@/router";
import { ICohortQueryCreate, sendQuery } from "@/services/cohort-query-service";
import { IProject } from "@/services/project-service";
import { useProjectStore } from "@/store/project";
import { containsForbiddenCommands } from "@/utils/cohort/query";
import { Snackbar } from "@/utils/snackbar";

import CohortAggregateCard from "./CohortAggregateCard.vue";
import PerTrustResponse from "./PerTrustResponse.vue";
import QueryResultCharts from "./QueryResultCharts.vue";

const route = useRoute();
const projectStore = useProjectStore();
const { isViewer } = usePermissions();

const queryId = ref<string>("");
const project = ref<IProject>();
const formSubmitting = ref<boolean>(false);

const emits = defineEmits(["UpdateProject", "submittingChange"]);

watch(formSubmitting, (v) => emits("submittingChange", v));

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

            // Ask the parent to reload the project. formSubmitting stays
            // true here — the watch below flips it false once the project
            // store carries the just-submitted query. Otherwise PerTrustResponse
            // would briefly see submitting=false + project.query=null and
            // render its "no query" empty state, flashing the trust rows out
            // and back in.
            emits("UpdateProject");

            Snackbar.show({
                type: "success",
                text: "Cohort query has been sent to trusts and queued for processing",
                title: "Cohort Query Sent",
                actionText: "View Project",
                action: () => router.push({ path: `/project/${project.value?.id}` })
            });

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
            text: "There was a problem running this cohort query:\n\n " + (e as Error).message
        });

        formSubmitting.value = false;

        return;
    }
};

// Spans the submit + the parent's project reload — only complete when the
// project store carries the just-submitted query. Belt-and-braces: a stale
// queryId watch would never fire, but the user can navigate away.
watch(
    () => projectStore.project?.query?.id,
    (qid) => {
        if (qid && qid === queryId.value && formSubmitting.value) {
            formSubmitting.value = false;
        }
    }
);

const queryLocked = computed(() => project?.value?.status !== "UNSTAGED");

// "Last run 14:32 today by R. Patel" — built from the persisted query metadata.
// `today`/`yesterday`/an absolute date is picked based on the calendar day in the
// viewer's locale; `createdBy` is the display name resolved server-side from
// UserProfile (null for legacy rows saved before created_by existed).
const lastRunLine = computed(() => {
    const q = project.value?.query;
    if (!q?.created) return "";
    const ts = new Date(q.created);
    if (Number.isNaN(ts.getTime())) return "";
    const time = ts.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false
    });
    const now = new Date();
    const sameDay = ts.toDateString() === now.toDateString();
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    const dayLabel = sameDay
        ? "today"
        : ts.toDateString() === yesterday.toDateString()
            ? "yesterday"
            : `on ${ts.toLocaleDateString()}`;
    const by = q.createdBy ? ` by ${q.createdBy}` : "";

    return `Last run ${time} ${dayLabel}${by}`;
});
</script>
