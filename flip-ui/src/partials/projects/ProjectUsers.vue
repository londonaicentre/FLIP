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
    <AiAlert
        v-if="!readonly"
        text="Any users added below will be granted access to this project."
        variant="info"
        class="w-full mb-4"
        data-test="add-user-project-info"
    />
    <Form
        v-if="!readonly"
        :validation-schema="schema"
        class="flex flex-row items-center mb-4"
        @submit="submit"
    >
        <div class="w-full">
            <AiInput
                name="email"
                type="email"
                label="Add user"
                data-test="add-user-project-input"
                @change="userIsDirty = true"
            >
                <template #labelRight>
                    <div
                        data-test="add-user-optional-text"
                        class="mb-1 text-sm text-right text-gray-400"
                    >
                        Optional
                    </div>
                </template>
                <template #inputButton>
                    <AiButton
                        light
                        :loading="formSubmit"
                        :disabled="formSubmit || !userIsDirty"
                        type="submit"
                        data-test="add-user-project-btn"
                        class="float-right mb-0.5 ml-2"
                    >
                        Add
                    </AiButton>
                </template>
            </AiInput>
        </div>
    </Form>
    <div
        v-if="invalidUser.length"
        data-test="invalid-user-project-list"
    >
        <div
            v-for="row in invalidUser"
            :key="row"
            class="w-full mb-2"
        >
            <AiAlert
                :text="row"
                variant="error"
            />
        </div>
    </div>
    <div v-if="readonly" class="mb-1 text-sm font-bold text-gray-700 dark:text-gray-400">
        Additional Project Users
    </div>
    <div class="flex flex-col-reverse items-start w-full h-full mb-2 space-y-2 space-y-reverse overflow-y-auto">
        <div
            v-for="(row, i) in userList"
            :key="row.id"
            data-test="added-user-project-list"
            class="flex flex-row items-center w-full px-4 py-2 border border-gray-300 rounded-md dark:border-gray-600"
        >
            <div class="text-sm font-bold truncate grow" :data-test="`added-user-${i}`">
                {{ row.email }}
            </div>
            <button
                v-if="!readonly"
                v-tippy="{content: 'Remove User'}"
                class="inline-block"
                :data-test="`remove-user-${i}-project-btn`"
                @click="() => removeUser(row.id)"
            >
                <icon-mdi-close
                    class="w-5 h-5 text-gray-500 transition dark:text-gray-400 hover:text-red-500 dark:hover:text-red-400"
                />
            </button>
        </div>
        <div
            v-if="!userList.length"
            class="flex flex-row items-center justify-center w-full p-6 text-sm text-gray-600 border-2 border-gray-300 border-dashed rounded-md dark:text-gray-400 dark:border-gray-600"
        >
            No Project Users
        </div>
    </div>
</template>

<script setup lang="ts">
import { AxiosError } from "axios";
import { Form } from "vee-validate";
import { ref } from "vue";
import { directive as vTippy } from "vue-tippy";
import { object } from "yup";

import AiAlert from "@/components/AiAlert/AiAlert.vue";
import AiButton from "@/components/AiButton/AiButton.vue";
import AiInput from "@/components/AiInput/AiInput.vue";
import { IProjectUser, validateUser } from "@/services/user-service";
import { emailValidation } from "@/utils/forms/validation";

export interface IProjectUsersProps {
    users: IProjectUser[];
    readonly?: boolean;
}

interface IAddUser {
    email: string
}

const props = withDefaults(
    defineProps<IProjectUsersProps>(),
    { readonly: false }
);

const formSubmit = ref<boolean>(false);
let enteredEmail: string;
const userList = ref<IProjectUser[]>(props.users);
const invalidUser = ref<string[]>([]);
const userIsDirty = ref(false);

const emit = defineEmits(["updatedUsers"]);

const schema = object().shape({ email: emailValidation });

const submit = async(v: unknown, { resetForm }: {resetForm: () => void} ): Promise<void> => {
    try {
        userIsDirty.value = false;
        formSubmit.value = true;
        const { email } = v as IAddUser;

        enteredEmail = email;

        if (userList.value.some(u => u.email === email)) {
            return handleError(`${email} has already been added to the list`);
        }

        const user = await validateUser(email);

        if (user) {
            if (user.isDisabled) {
                return handleError(`${email} is disabled`);
            }

            userList.value.push(user);
        }

        emit("updatedUsers", userList.value);

        resetForm();
    } catch (e) {
        console.log(e);
        if ((e as AxiosError).response?.status === 404) {
            return handleError(`${enteredEmail} cannot be found`);
        }

        return handleError("Something went wrong when retrieving the user");
    } finally {
        formSubmit.value = false;
    }
};

const removeUser = async(id: string): Promise<void> => {
    try {
        formSubmit.value = true;

        const index = userList.value.findIndex(u => u.id === id);

        if (index > -1) {
            userList.value.splice(index, 1);
            formSubmit.value = false;

            emit("updatedUsers", userList.value);
        }

    } catch (e) {
        return handleError("Something went wrong when removing the user");
    } finally {
        formSubmit.value = false;
    }
};

const handleError = (err: string): void => {
    invalidUser.value.push(err);

    setTimeout(() => {
        invalidUser.value.shift();
    }, 5_000);
};
</script>
