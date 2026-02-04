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
    <TransitionRoot as="template" :show="open">
        <Dialog as="div" class="fixed inset-0 z-10 overflow-hidden" :unmount="true" @close="closeModal">
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

                <Form
                    class="fixed inset-y-0 right-0 flex max-w-full pl-10"
                    :validation-schema="schema"
                    @submit="submitForm"
                >
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
                            <DialogPanel class="flex flex-col h-full bg-white dark:bg-gray-800 divide-y divide-gray-100 dark:divide-gray-700 shadow-xl dark:ring-white/20 dark:ring-1">
                                <div class="p-4 bg-primary-500 dark:bg-gray-900 sm:px-6">
                                    <div class="flex items-center justify-between">
                                        <DialogTitle class="text-xl font-bold font-heading text-primary-100 dark:text-gray-300">
                                            Create Project
                                        </DialogTitle>
                                        <div class="flex items-start ml-3 h-7 text-primary-300 dark:text-gray-400">
                                            <button
                                                type="button"
                                                class="cursor-pointer hover:text-primary-100 transition focus:outline-none focus:ring-1 rounded focus:ring-primary-400"
                                                tabindex="0"
                                                @click="closeModal"
                                            >
                                                <span class="sr-only">Close</span>
                                                <icon-mdi-close />
                                            </button>
                                        </div>
                                    </div>
                                    <div class="mt-1">
                                        <p class="text-sm text-primary-200 dark:text-gray-500">
                                            Enter your projects details below.
                                        </p>
                                    </div>
                                </div>
                                <div class="flex flex-col flex-1 min-h-0 pb-6 overflow-y-auto">
                                    <div class="relative flex-1 px-4 mt-6 sm:px-6">
                                        <div class="w-full space-y-4">
                                            <AiInput
                                                name="name"
                                                type="text"
                                                label="Project Name"
                                                data-test="project-name"
                                                hint="Used to identify this project throughout the system."
                                            />
                                            <AiTextArea
                                                name="description"
                                                type="text"
                                                label="Project Description"
                                                data-test="project-description"
                                                placeholder="Optional description"
                                                hint="A small description of what your project is trying to achieve."
                                            />
                                        </div>

                                        <div class="mt-6">
                                            <ProjectUsers :users="users" @updated-users="handleUsers" />
                                        </div>
                                    </div>
                                </div>
                                <div class="flex justify-end flex-shrink-0 p-4 space-x-4 bg-gray-50 dark:bg-gray-900">
                                    <AiButton :data-test="'close-create-project-btn'" @click="closeModal">
                                        Cancel
                                    </AiButton>
                                    <AiButton
                                        primary
                                        class="ml-2"
                                        :loading="formSubmitting"
                                        :disabled="formSubmitting"
                                        :data-test="'create-project-btn'"
                                        type="submit"
                                    >
                                        Create Project
                                    </AiButton>
                                </div>
                            </DialogPanel>
                        </div>
                    </TransitionChild>
                </Form>
            </div>
        </dialog>
    </TransitionRoot>
</template>

<script setup lang="ts">
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from "@headlessui/vue";
import { Form } from "vee-validate";
import { ref } from "vue";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiDialogOverlay from "@/components/AiDialogOverlay/AiDialogOverlay.vue";
import AiInput from "@/components/AiInput/AiInput.vue";
import AiTextArea from "@/components/AiTextArea/AiTextArea.vue";
import { routeChange } from "@/router";
import { createProject, IProjectCreate } from "@/services/project-service";
import { IProjectUser } from "@/services/user-service";
import { useModalsStore } from "@/store/modals";
import { projectSchema } from "@/utils/forms/validation";
import { Snackbar } from "@/utils/snackbar";

import ProjectUsers from "./ProjectUsers.vue";

interface ICreateProjectModalProps {
    open: boolean;
}

defineProps<ICreateProjectModalProps>();

const modalStore = useModalsStore();
const schema = projectSchema;

const formSubmitting = ref(false);
let users: IProjectUser[] = [];

const handleUsers = (updatedUsers: IProjectUser[]) => {
    users = updatedUsers;
};

const closeModal = () => {
    modalStore.toggleCreateProject();
};

const submitForm = async(v: unknown) => {
    if(formSubmitting.value) {
        return;
    }

    formSubmitting.value = true;

    try {
        const values = v as IProjectCreate;

        values.users = users.map(u => {
            return u.id;
        });

        const { id: projectId } = await createProject("/projects", values as IProjectCreate);

        modalStore.toggleCreateProject();

        Snackbar.show({
            type: "success",
            title: "Success",
            text: "Project created successfully"
        });

        routeChange.viewProject(projectId);
    } catch (e) {
        modalStore.toggleCreateProject();

        Snackbar.show({
            type: "error",
            text: "There was a problem creating this project.",
            title: "Error"
        });
    }

    formSubmitting.value = false;
};
</script>
