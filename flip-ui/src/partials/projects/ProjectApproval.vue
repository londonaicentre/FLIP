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
        <div class="p-4">
            <h2 class="text-lg font-semibold font-heading grow leading-loose">
                Project Approval
            </h2>
        </div>
        <Form
            v-if="canApprove || projectApproved"
            v-slot="{errors}"
            :validation-schema="schema"
            @submit="approveProject"
        >
            <div class="w-full gap-3 text-sm">
                <ul role="list" class="border-gray-200 divide-y divide-gray-200 dark:divide-gray-700 dark:border-gray-700 border-y">
                    <li v-for="(trust, idx) in approvedTrusts" :key="trust.id">
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
                                    v-if="!projectApproved"
                                    name="trusts"
                                    :data-test="`trust-staged-${idx}`"
                                    :value="trust.id"
                                    hide-error
                                    :disabled="!hasPermissionToApprove || approving"
                                    :label="{ enabled: 'Approved', disabled: 'Not Approved' }"
                                />
                                <template v-else>
                                    <div
                                        v-if="trust.approved"
                                        class="flex flex-row items-center justify-between gap-2 px-2 py-1 border-2 border-gray-200 dark:border-gray-700 rounded ring-2 ring-offset-2 ring-green-600/70 dark:ring-green-500 dark:ring-offset-gray-900"
                                    >
                                        <span class="text-xs font-bold">Approved</span>
                                    </div>
                                    <div
                                        v-if="!trust.approved"
                                        class="flex flex-row items-center justify-between gap-2 px-2 py-1 border-2 border-gray-200 dark:border-gray-700 rounded ring-2 ring-offset-2 ring-red-600/70 dark:ring-red-500 dark:ring-offset-gray-900"
                                    >
                                        <span class="text-xs font-bold">Not Approved</span>
                                    </div>
                                </template>
                            </div>
                        </div>
                    </li>
                    <div v-if="errors.trusts && !projectApproved" class="px-4 py-2 text-sm text-right text-red-600 dark:text-red-400">
                        {{ errors.trusts }}
                    </div>
                </ul>
            </div>
            <AiGuard :permissions="['CanApproveProjects']">
                <div v-if="canApprove && !projectApproved" class="p-4">
                    <div v-if="!projectApproved" class="inline-flex justify-end w-full space-x-4">
                        <AiButton
                            class="ml-2"
                            primary
                            small
                            data-test="approve-project-btn"
                            :disabled="approving"
                            :loading="approving"
                            type="submit"
                        >
                            Save Trust Approvals
                        </AiButton>
                    </div>
                </div>
            </AiGuard>
        </Form>
    </AiCard>
</template>

<script setup lang="ts">
import { Form } from "vee-validate";
import { computed } from "vue";
import { array, lazy, object, string } from "yup";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import AiGuard from "@/components/AiGuard/AiGuard.vue";
import AiSwitch from "@/components/AiSwitch/AiSwitch.vue";
import { IProjectTrust } from "@/services/project-service";
import { useAuthStore } from "@/store/auth";

interface IProjectApprovalProps {
    approvedTrusts: IProjectTrust[];
    projectApproved: boolean;
    approving: boolean;
    canApprove: boolean;
}

const authStore = useAuthStore();

defineProps<IProjectApprovalProps>();

const emits = defineEmits(["approveProject", "stageProject"]);

const schema = object().shape({
    trusts: lazy(trusts =>
        (Array.isArray(trusts)
            ?
            array()
                .of(string().required())
                .min(1, "You must select a minimum of one trust when approving.")
                .required("You must select a minimum of one trust when approving.")
            :
            string().required("You must select a minimum of one trust when approving.")))
});

const hasPermissionToApprove = computed(() => {
    return authStore.hasPermissions(["CanApproveProjects"]);
});

const approveProject = (v: unknown) => {
    const values = v as { trusts: string[] };
    emits("approveProject", values.trusts);
};
</script>
