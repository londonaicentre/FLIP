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
                            <Form
                                :validation-schema="schema"
                                class="flex flex-col h-full bg-white divide-y divide-gray-100 shadow-xl dark:bg-gray-800 dark:divide-gray-700 dark:ring-1 dark:ring-white/20"
                                @submit="updateProject"
                            >
                                <div class="p-4 bg-primary-500 sm:px-6 dark:bg-gray-900">
                                    <div class="flex items-center justify-between">
                                        <DialogTitle class="text-xl font-bold font-heading text-primary-100 dark:text-gray-300">
                                            Edit project details
                                        </DialogTitle>
                                        <div class="flex items-start ml-3 h-7 text-primary-300 dark:text-gray-400">
                                            <button
                                                type="button"
                                                class="transition rounded cursor-pointer hover:text-primary-100 focus:outline-none focus:ring-1 focus:ring-primary-400"
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
                                            Edit your projects details below.
                                        </p>
                                    </div>
                                </div>
                                <div class="flex flex-col flex-1 min-h-0 pb-6 overflow-y-auto">
                                    <div class="relative flex-1 px-4 mt-6 sm:px-6">
                                        <div class="w-full space-y-4">
                                            <AiAlert
                                                v-if="!projectUnstaged"
                                                variant="info"
                                                text="This project has been staged and can no longer be edited."
                                            />
                                            <div>
                                                <AiInput
                                                    name="name"
                                                    type="text"
                                                    label="Name"
                                                    data-test="project-name"
                                                    :initial-value="name"
                                                    :input-props="{
                                                        disabled: !projectUnstaged,
                                                        readonly: !projectUnstaged
                                                    }"
                                                />
                                            </div>
                                            <div>
                                                <AiTextArea
                                                    name="description"
                                                    type="text"
                                                    label="Description"
                                                    data-test="project-description"
                                                    :initial-value="description"
                                                    :input-props="{
                                                        disabled: !projectUnstaged,
                                                        readonly: !projectUnstaged
                                                    }"
                                                />
                                            </div>
                                        </div>

                                        <div class="mt-6">
                                            <ProjectUsers
                                                :users="users"
                                                :readonly="!projectUnstaged"
                                                @updated-users="handleUpdatedUsers"
                                            />
                                        </div>
                                    </div>
                                </div>
                                <div v-if="canDeleteProject()" class="p-4 sm:px-6 bg-body dark:bg-gray-900">
                                    <div class="flex items-center justify-between">
                                        <DialogTitle class="font-bold font-heading">
                                            Advanced Options
                                        </DialogTitle>
                                    </div>
                                    <div class="mt-2">
                                        <AiButton text data-test="delete-project-btn" @click="confirmDeleteOpen = true">
                                            <icon-mdi-delete class="mr-2 text-red-500 dark:text-red-400" />
                                            <span class="text-red-500 dark:text-red-400">
                                                Delete Project
                                            </span>
                                        </AiButton>
                                    </div>
                                </div>
                                <div class="flex justify-end flex-shrink-0 p-4 space-x-4 bg-gray-50 dark:bg-gray-900">
                                    <AiButton @click="closeDrawer">
                                        Close
                                    </AiButton>
                                    <AiButton
                                        v-if="projectUnstaged"
                                        primary
                                        :loading="updating"
                                        :disabled="updating"
                                        data-test="update-project-btn"
                                        type="submit"
                                    >
                                        Update Project
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
                :continue-action="confirmDeleteProject"
                title="Are you sure you want to delete this project?"
                :confirmation-text="getConfirmDeleteProjectText()"
                placeholder="Project name"
                :submitting="submittingDeleteProject"
                @close-modal="confirmDeleteOpen = false"
            />
        </Dialog>
    </TransitionRoot>
</template>

<script lang="ts" setup>
import { Dialog, DialogTitle, TransitionChild, TransitionRoot } from "@headlessui/vue";
import { Form } from "vee-validate";
import { onMounted, ref } from "vue";

import AiAlert from "@/components/AiAlert/AiAlert.vue";
import AiButton from "@/components/AiButton/AiButton.vue";
import AiDialogOverlay from "@/components/AiDialogOverlay/AiDialogOverlay.vue";
import AiConfirmModal from "@/components/AiModal/AiConfirmModal.vue";
import router from "@/router";
import { deleteProject, IProjectUser } from "@/services/project-service";
import { useAuthStore } from "@/store/auth";
import { useErrorStore } from "@/store/error";
import { projectSchema } from "@/utils/forms/validation";
import { Snackbar } from "@/utils/snackbar";

import ProjectUsers from "./ProjectUsers.vue";

export interface IEditProject {
    name: string;
    description: string;
    users: string[];
}

interface IEditProjectDrawerProps {
    show: boolean;
    name: string;
    id: string;
    description: string;
    projectUnstaged: boolean;
    updating: boolean;
    users: IProjectUser[];
    ownerId: string;
}

const schema = projectSchema;
const authStore = useAuthStore();

const props = defineProps<IEditProjectDrawerProps>();

let userList: string[];

onMounted(() => {
    userList = props.users.map(u => u.id);
});

const emit = defineEmits(["close", "delete", "save"]);

const confirmDeleteOpen = ref(false);
const submittingDeleteProject = ref(false);

const getConfirmDeleteProjectText = () => `
<div class="my-4 space-y-2">
    <strong>Any active training jobs performed on the models within the project will be stopped. This can not be undone.</strong>
    <p>Your username will be recorded against this action.</p>
    <p>To delete this project, enter <code class="p-1 font-bold leading-loose tracking-tight bg-gray-100 rounded dark:bg-gray-700 dark:text-primary-200">${props.name}</code> below.</p>
</div>`;

const closeDrawer = () => {
    emit("close");
};

const updateProject = (values: unknown) => {
    emit("save", {
        ...values as IEditProject,
        users: userList
    });
};

const handleUpdatedUsers = (latestUsers: IProjectUser[]) => {
    userList = latestUsers.map(u => u.id);
};

const confirmDeleteProject = async () => {

    submittingDeleteProject.value = true;

    try {
        await deleteProject(`/projects/${props.id}`);
    } catch {
        Snackbar.error({
            title: "Unable to delete this project",
            text: `The project "${props.name}" has not been deleted.`
        });

        useErrorStore().setError();

        submittingDeleteProject.value = false;

        confirmDeleteOpen.value = false;

        closeDrawer();

        return;
    }

    confirmDeleteOpen.value = false;

    submittingDeleteProject.value = false;

    router.push({ path: "/projects" });

    Snackbar.success({
        title: "Project deleted",
        text: `The project "${props.name}" has been deleted.`
    });
};

const canDeleteProject = () => {
    const userId = authStore.user?.attributes?.sub;
    const userHasPermission = authStore.hasPermissions(["CanDeleteAnyProject"]);

    return userId === props.ownerId || userHasPermission;
};

</script>
