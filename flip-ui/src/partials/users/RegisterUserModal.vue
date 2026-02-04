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

﻿<template>
    <TransitionRoot as="template" :show="dialog">
        <Dialog as="div" class="fixed inset-0 z-10" @close.capture="close">
            <div class="flex items-end justify-center h-screen min-h-screen px-4 pt-4 text-center sm:block sm:p-0">
                <TransitionChild
                    as="template"
                    enter="ease-out duration-300"
                    enter-from="opacity-0"
                    enter-to="opacity-100"
                    leave="ease-in duration-200"
                    leave-from="opacity-100"
                    leave-to="opacity-0"
                >
                    <AiDialogOverlay />
                </TransitionChild>
                <!-- This element is to trick the browser into centering the modal contents. -->
                <span class="inline-block align-middle sm:h-screen" aria-hidden="true">&#8203;</span>
                <TransitionChild
                    as="template"
                    enter="ease-out duration-300"
                    enter-from="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
                    enter-to="opacity-100 translate-y-0 sm:scale-100"
                    leave="ease-in duration-200"
                    leave-from="opacity-100 translate-y-0 sm:scale-100"
                    leave-to="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
                >
                    <div
                        class="inline-flex flex-col w-full max-w-lg max-h-screen p-4 text-left align-middle rounded-lg"
                    >
                        <div
                            class="inline-flex flex-col w-full transition-all transform bg-white rounded-lg shadow-xl dark:bg-gray-800"
                        >
                            <DialogTitle
                                as="h3"
                                class="px-8 py-4 text-lg font-bold leading-6 text-left text-gray-700 dark:text-gray-300"
                            >
                                {{ title }}
                            </DialogTitle>
                            <div class="flex flex-grow overflow-y-auto bg-white dark:bg-gray-800">
                                <div class="flex flex-col items-start w-full">
                                    <div class="w-full text-left">
                                        <div class="w-full px-8 py-4 space-y-4 overflow-y-auto text-sm font-normal leading-5 dark:text-gray-400">
                                            <p>The new user will be sent a temporary password.</p>
                                            <AiInput
                                                class="mt-2"
                                                data-test="email-field"
                                                type="email"
                                                name="email"
                                                placeholder="Email Address"
                                            />
                                            <AiChipSelect
                                                default-text="Please select a role"
                                                :error-message="errors?.selectedRoles"
                                                :options="mapRoleOptions()"
                                                :selected-options="fields"
                                                @push="push"
                                                @remove="remove"
                                                @validate="validateField('selectedRoles')"
                                            />
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <div class="px-4 py-3 bg-gray-100 rounded-b-lg dark:bg-gray-900 sm:px-6 sm:flex sm:flex-row-reverse sm:flex-shrink-0">
                                <AiButton
                                    data-test="register-user-confirm-btn"
                                    primary
                                    class="w-full ml-2 sm:w-auto"
                                    :loading="isSubmitting"
                                    @click="submitAction"
                                >
                                    Register User
                                </AiButton>
                                <AiButton
                                    class="w-full sm:w-auto"
                                    data-test="close-modal-btn"
                                    @click="close"
                                >
                                    Cancel
                                </AiButton>
                            </div>
                        </div>
                    </div>
                </TransitionChild>
            </div>
        </Dialog>
    </TransitionRoot>
</template>

<script setup lang="ts">
import { Dialog,
    DialogTitle,
    TransitionChild,
    TransitionRoot } from "@headlessui/vue";
import { useField, useFieldArray, useForm } from "vee-validate";
import { ref, watch } from "vue";
import { array, object, string } from "yup";

import AiDialogOverlay from "@/components/AiDialogOverlay/AiDialogOverlay.vue";
import AiChipSelect from "@/components/AiSelect/AiChipSelect.vue";
import { IOption } from "@/components/AiSelect/interfaces";
import { IRole } from "@/services/role-service";
import { IRegisterUserDto, registerUser } from "@/services/user-service";
import { useErrorStore } from "@/store/error";
import { Snackbar } from "@/utils/snackbar";

interface IRegisterUserModalProps {
    dialog: boolean,
    title: string,
    roles: IRole[]
}

interface RegisterUserForm {
    email: string;
    selectedRoles: string[];
}

const props = withDefaults(
    defineProps<IRegisterUserModalProps>(), {
        title: "Register User",
        dialog: false
    }
);

const emit = defineEmits(["closeModal", "onSuccess"]);

const schema = object().shape({
    email: string()
        .required("An email address is required")
        .email("Please enter a valid email address"),
    selectedRoles: array()
        .required("Select at least 1 role")
        .min(1, "Select at least 1 role")
});

const errorStore = useErrorStore();
const currentlySelected = ref();
const isSubmitting = ref(false);

const { errors, resetForm, validate, validateField } = useForm<RegisterUserForm>({ validationSchema: schema });
const { push, remove, fields } = useFieldArray("selectedRoles");
const email = useField("email");
useField("selectedRoles");

watch(currentlySelected, async (current) => {
    if (current) {
        if (!fields.value.some((field) => (field.value as IRole).id === current.id)) {
            push(current);
        }
        await validateField("selectedRoles");
    }
});

const close = () => {
    emit("closeModal", false);
    currentlySelected.value = undefined;
    resetForm();
};

const submitAction = async () => {
    isSubmitting.value = true;

    const { valid } = await validate();

    if (valid) {
        const user: IRegisterUserDto = {
            email: email.value.value as string,
            roles: fields.value.map(item => (item.value as IRole).id)
        };

        try {
            const result = await registerUser(user);

            if (!result.email) {
                Snackbar.error({
                    text: "There was an error, please try again.",
                    title: "User not registered"
                });
                errorStore.setError();
            } else {
                emit("onSuccess", false);
                Snackbar.success({
                    text: "The user has been registered successfully",
                    title: "User registered"
                });
            }
        } catch (e) {
            Snackbar.error({
                text: "There was an error, please try again.",
                title: "User not registered"
            });
            errorStore.setError();
        }
        close();
    }

    isSubmitting.value = false;
};

const mapRoleOptions = (): IOption[] => {
    return props.roles.map((item) => {
        const option: IOption = {
            id: item.id,
            description: item.rolename
        };

        return option;
    });
};
</script>
