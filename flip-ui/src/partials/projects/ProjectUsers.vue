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
                        class="mb-1 text-sm text-right text-gray-500 dark:text-gray-300"
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
    <div class="mb-1 text-sm font-bold text-gray-700 dark:text-gray-300" data-test="project-users-heading">
        Project Users
    </div>
    <!-- The owner always has access and cannot be removed, so their row has no remove button. -->
    <div
        data-test="project-owner-row"
        class="flex flex-row items-center w-full gap-2 px-4 py-2 mb-2 border border-gray-300 rounded-md
               dark:border-dark-border-strong"
    >
        <div class="text-sm font-bold truncate grow">
            {{ ownerEmail }}
        </div>
        <span
            class="px-2 py-0.5 text-xs font-semibold rounded-full shrink-0 text-primary-500 bg-primary-100
                   dark:text-primary-200 dark:bg-dark-raised"
        >
            Owner
        </span>
    </div>
    <div class="flex flex-col-reverse items-start w-full h-full mb-2 space-y-2 space-y-reverse overflow-y-auto">
        <div
            v-for="(row, i) in members"
            :key="row.id"
            data-test="added-user-project-list"
            class="flex flex-row items-center w-full px-4 py-2 border border-gray-300 rounded-md dark:border-dark-border-strong"
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
                    class="w-5 h-5 text-gray-500 transition dark:text-gray-300 hover:text-red-500 dark:hover:text-red-400"
                />
            </button>
        </div>
    </div>
</template>

<script setup lang="ts">
import { AxiosError } from "axios";
import { Form } from "vee-validate";
import { computed, ref } from "vue";
import { directive as vTippy } from "vue-tippy";
import { object } from "yup";

import AiAlert from "@/components/AiAlert/AiAlert.vue";
import AiButton from "@/components/AiButton/AiButton.vue";
import AiInput from "@/components/AiInput/AiInput.vue";
import { IProjectUserLookup, lookupProjectUser } from "@/services/user-service";
import { emailValidation } from "@/utils/forms/validation";

export interface IProjectUsersProps {
    // Existing members (`GET /projects/{id}`) and newly looked-up ones (`GET /users/lookup`) both
    // carry id / email / isDisabled only (FLIP#907, FLIP#996).
    users?: IProjectUserLookup[];
    // Listed first and never removable. `users` may or may not contain the owner: an existing
    // project's does (the hub adds the owner), a new project's does not.
    ownerId: string;
    ownerEmail: string;
    readonly?: boolean;
}

interface IAddUser {
    email: string
}

const props = withDefaults(
    defineProps<IProjectUsersProps>(),
    {
        readonly: false,
        users: () => []
    }
);

const formSubmit = ref<boolean>(false);
// Copy rather than alias `props.users`: the list is mutated in place on add/remove, which would
// otherwise mutate the parent's prop. The owner stays in it when present: editing a project
// replaces its whole access list with what is emitted here.
const userList = ref<IProjectUserLookup[]>([...props.users]);
const members = computed(() => userList.value.filter(u => u.id !== props.ownerId));
const invalidUser = ref<string[]>([]);
const userIsDirty = ref(false);

const emit = defineEmits(["updatedUsers"]);

const schema = object().shape({ email: emailValidation });

const submit = async(v: unknown, { resetForm }: {resetForm: () => void} ): Promise<void> => {
    const { email } = v as IAddUser;

    try {
        userIsDirty.value = false;
        formSubmit.value = true;

        if (email === props.ownerEmail || userList.value.some(u => u.email === email)) {
            return handleError(`${email} has already been added to the list`);
        }

        const user = await lookupProjectUser(email);

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
            return handleError(`${email} cannot be found`);
        }

        return handleError("Something went wrong when retrieving the user");
    } finally {
        formSubmit.value = false;
    }
};

const removeUser = (id: string): void => {
    try {
        formSubmit.value = true;

        const index = userList.value.findIndex(u => u.id === id);

        if (index > -1) {
            userList.value.splice(index, 1);

            emit("updatedUsers", userList.value);
        }

    } catch {
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
