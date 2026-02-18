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
    <TransitionRoot as="template" :show="show">
        <Dialog as="div" class="fixed inset-0 z-10 overflow-hidden" :unmount="true" @close="closeDrawer">
            <input class="hidden">
            <div class="absolute inset-0 overflow-hidden">
                <TransitionChild
                    as="template"
                    enter="ease-in-out duration-500"
                    enter-from="opacity-0"
                    enter-to="opacity-100"
                    leave="ease-in-out duration-500"
                    leave-from="opacity-100"
                    leave-to="opacity-0"
                >
                    <AiDialogOverlay />
                </TransitionChild>

                <div class="fixed inset-y-0 right-0 flex max-w-full pl-10">
                    <TransitionChild
                        as="template"
                        enter="transform transition ease-in-out duration-500 sm:duration-700"
                        enter-from="translate-x-full"
                        enter-to="translate-x-0"
                        leave="transform transition ease-in-out duration-500 sm:duration-700"
                        leave-from="translate-x-0"
                        leave-to="translate-x-full"
                    >
                        <div class="w-screen max-w-4xl">
                            <Form :validation-schema="schema" class="flex flex-col h-full bg-white divide-y divide-gray-100 shadow-xl dark:bg-gray-800 dark:divide-gray-700 dark:ring-1 dark:ring-white/20" @submit="update">
                                <div class="p-4 bg-primary-500 dark:bg-gray-900 sm:px-6">
                                    <div class="flex items-center justify-between">
                                        <DialogTitle class="text-xl font-bold font-heading text-primary-100">
                                            Edit model details
                                        </DialogTitle>
                                        <div class="flex items-start ml-3 h-7 text-primary-300 dark:text-gray-400">
                                            <button
                                                type="button"
                                                class="!text-primary-300 cursor-pointer hover:text-primary-100 transition focus:outline-none focus:ring-1 rounded focus:ring-primary-400"
                                                tabindex="0"
                                                @click="closeDrawer"
                                            >
                                                <span class="sr-only">Close</span>
                                                <icon-mdi-close />
                                            </button>
                                        </div>
                                    </div>
                                    <div class="mt-1">
                                        <p class="text-sm text-primary-200 dark:text-gray-500">
                                            Edit your model details below.
                                        </p>
                                    </div>
                                </div>
                                <AiAlert
                                    v-if="!modelPending"
                                    variant="info"
                                    text="This training has been started so you can no longer edit model details."
                                    :rounded="false"
                                    :bordered="false"
                                />
                                <div class="flex flex-col flex-1 min-h-0 pb-6 overflow-y-auto">
                                    <div class="relative flex-1 px-4 mt-6 sm:px-6">
                                        <div class="w-full space-y-4">
                                            <div>
                                                <AiInput
                                                    name="name"
                                                    type="text"
                                                    label="Name"
                                                    data-test="model-name"
                                                    :initial-value="name"
                                                    :input-props="{
                                                        disabled: !modelPending,
                                                        readonly: !modelPending
                                                    }"
                                                />
                                            </div>
                                            <div>
                                                <AiTextArea
                                                    name="description"
                                                    type="text"
                                                    label="Description"
                                                    data-test="model-description"
                                                    :initial-value="description"
                                                    :input-props="{
                                                        disabled: !modelPending,
                                                        readonly: !modelPending
                                                    }"
                                                />
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div v-if="canDeleteModel()" class="p-4 sm:px-6 bg-body dark:bg-gray-900">
                                    <div class="flex items-center justify-between">
                                        <DialogTitle class="font-bold font-heading">
                                            Advanced Options
                                        </DialogTitle>
                                    </div>
                                    <div class="mt-2">
                                        <AiButton text data-test="delete-model-btn" @click="confirmDeleteOpen = true">
                                            <icon-mdi-delete class="mr-2 text-red-500" />
                                            <span class="text-red-500">
                                                Delete Model
                                            </span>
                                        </AiButton>
                                    </div>
                                </div>
                                <div class="flex justify-end flex-shrink-0 p-4 space-x-4 bg-gray-50 dark:bg-gray-900">
                                    <AiButton
                                        @click="closeDrawer"
                                    >
                                        Close
                                    </AiButton>
                                    <AiButton
                                        v-if="modelPending"
                                        primary
                                        :loading="updating"
                                        :disabled="updating"
                                        data-test="update-model-btn"
                                        type="submit"
                                    >
                                        Update Model
                                    </AiButton>
                                </div>
                            </Form>
                        </div>
                    </TransitionChild>
                </div>
            </div>
            <AiConfirmModal
                :dialog="confirmDeleteOpen"
                :typing-confirmation="name"
                :continue-action="confirmDeleteModel"
                title="Are you sure you want to delete this model?"
                :confirmation-text="getconfirmDeleteModelText()"
                placeholder="Model name"
                :submitting="submittingDeleteModel"
                @close-modal="confirmDeleteOpen = false"
            />
        </Dialog>
    </TransitionRoot>
</template>

<script lang="ts" setup>
import { Dialog, DialogTitle, TransitionChild, TransitionRoot } from "@headlessui/vue";
import { Form } from "vee-validate";
import { ref } from "vue";
import { useRoute } from "vue-router";

import AiAlert from "@/components/AiAlert/AiAlert.vue";
import AiButton from "@/components/AiButton/AiButton.vue";
import AiDialogOverlay from "@/components/AiDialogOverlay/AiDialogOverlay.vue";
import AiConfirmModal from "@/components/AiModal/AiConfirmModal.vue";
import router from "@/router";
import { deleteModel } from "@/services/model-service";
import { useAuthStore } from "@/store/auth";
import { useErrorStore } from "@/store/error";
import { useProjectStore } from "@/store/project";
import { modelSchema } from "@/utils/forms/validation";
import { Snackbar } from "@/utils/snackbar";

export interface IEditModel {
    name: string;
    description: string;
}

interface IEditModelDrawerProps {
    id: string;
    show: boolean;
    name: string;
    description: string;
    modelPending: boolean;
    updating: boolean;
    ownerId: string;
}

const authStore = useAuthStore();
const errorStore = useErrorStore();
const projectStore = useProjectStore();
const route = useRoute();

const schema = modelSchema;

const props = defineProps<IEditModelDrawerProps>();

const emit = defineEmits(["close", "save"]);

const confirmDeleteOpen = ref(false);
const submittingDeleteModel = ref(false);

const getconfirmDeleteModelText = () => `
<div class="my-4 space-y-2">
    <strong>Training for this model will also be stopped if active. This can not be undone.</strong>
    <p>Your username will be recorded against this action.</p>
    <p>To delete this model, enter <code class="p-1 font-bold tracking-tight bg-gray-100 rounded dark:bg-gray-700 dark:text-primary-200">${props.name}</code> below.</p>
</div>`;

const closeDrawer = () => {
    emit("close");
};

const update = (values: unknown) => {
    emit("save", { ...values as IEditModel });
};

const confirmDeleteModel = async () => {

    submittingDeleteModel.value = true;

    try {
        await deleteModel(`/model/${props.id}`);
    } catch {
        Snackbar.error({
            title: "Unable to delete this model",
            text: `The model "${props.name}" has not been deleted.`
        });

        errorStore.setError();

        submittingDeleteModel.value = false;

        confirmDeleteOpen.value = false;

        closeDrawer();

        return;
    }

    confirmDeleteOpen.value = false;

    submittingDeleteModel.value = false;

    router.push({ path: `/project/${route.params.projectId}` });

    Snackbar.success({
        title: "Model deleted",
        text: `The model "${props.name}" has been deleted.`
    });
};

const canDeleteModel = () => {
    const userHasPermission = authStore.hasPermissions(["CanManageProjects"]);

    return userHasPermission || isOwnerOrHasAccess();
};

const isOwnerOrHasAccess = () => {
    const projectOwner = props.ownerId;
    const currentUserId = authStore.user?.userId;
    const projectUsers = projectStore?.getProject?.users;

    return projectOwner === currentUserId || projectUsers?.map(u => u.id).includes(currentUserId as string);
};

</script>
