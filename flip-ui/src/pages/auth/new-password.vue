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

﻿<route lang="yaml">
meta:
    layout: AuthLayout
</route>

<template>
    <div v-if="!passwordChanged" class="flex flex-col h-full">
        <h1
            class="mb-5 text-xl font-heading md:text-2xl"
        >
            Please choose a new password
        </h1>
        <Form
            :validation-schema="schema"
            class="flex flex-col flex-grow p-0 mr-1 space-y-4"
            @submit="submit"
        >
            <div class="absolute right-0 mt-4 mr-4">
                <icon-mdi-information-outline
                    v-tippy="{ content: passwordRequirements }"
                    class="text-gray-500"
                />
            </div>
            <AiInput
                name="password"
                type="password"
                data-test="new-password"
                label="Enter a new password"
                :pre-icon="LockOutline"
            />
            <AiInput
                name="passwordConfirmation"
                type="password"
                data-test="confirm-new-password"
                label="Confirm new password"
                :pre-icon="LockOutline"
            />
            <div class="flex-grow" />
            <div class="flex flex-row">
                <div class="flex-grow" />
                <AiButton
                    primary
                    data-test="change-password-btn"
                    :loading="buttonLoader"
                    type="submit"
                >
                    Change Password
                </AiButton>
            </div>
        </Form>
    </div>
    <div v-if="passwordChanged" class="flex flex-col h-full">
        <h1
            class="mb-5 text-xl font-heading md:text-2xl"
        >
            Success
        </h1>
        <div class="flex flex-col flex-grow p-0 mr-1 space-y-4">
            <span class="text-sm font-bold leading-5" data-test="password-changed-message">
                Your password has been changed
            </span>
            <div class="flex-grow" />
            <div class="flex flex-row">
                <div class="flex-grow" />
                <AiButton
                    primary
                    data-test="login-btn"
                    :loading="buttonLoader"
                    @click="LogIn"
                >
                    Log in
                </AiButton>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { Form } from "vee-validate";
import { onBeforeMount, ref } from "vue";
import { object } from "yup";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiInput from "@/components/AiInput/AiInput.vue";
import { routeChange } from "@/router";
import { useAuthStore } from "@/store/auth";
import { useErrorStore } from "@/store/error";
import { isUserUnconfirmedCheck } from "@/utils/auth";
import { passwordValidation } from "@/utils/forms/validation";
import { Snackbar } from "@/utils/snackbar";
import LockOutline from "~icons/mdi/lock-outline";

const authStore = useAuthStore();
const errorStore = useErrorStore();
const buttonLoader = ref(false);
const passwordChanged = ref(false);
const passwordRequirements = `Password requirements:
• A minimum of 8 characters
• Contains at least 1 number
• Contains at least 1 uppercase letter
• Contains at least 1 lowercase letter
• Contains at least 1 special character`;

const schema = object().shape({
    password: passwordValidation,
    passwordConfirmation: passwordValidation
        .test("password-match", "Your passwords do not match", function () {
            return this.parent.password === this.parent.passwordConfirmation;
        })
});

interface INewPassword {
    password: string;
    passwordConfirmation: string;
}

onBeforeMount(async () => {
    if (!(await isUserUnconfirmedCheck(authStore))) {
        routeChange.viewProjects();
    }
});

/**
 * Methods
 */

const submit = async (v: unknown): Promise<void> => {
    buttonLoader.value = true;

    const values = v as INewPassword;

    try {
        await authStore.changePassword(values.password);
    } catch (e) {
        Snackbar.show({
            type: "error",
            title: "Error",
            text: "There was a problem changing your password."
        });

        errorStore.setError();

        buttonLoader.value = false;

        return;
    }

    passwordChanged.value = true;
    buttonLoader.value = false;
};

const LogIn = async () => {
    buttonLoader.value = true;
    routeChange.gotoLogin();
    buttonLoader.value = false;
};
</script>
