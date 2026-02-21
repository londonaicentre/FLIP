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

﻿<route lang="yaml">
    name: ChangePassword
    meta:
        layout: AuthLayout
</route>

<template>
    <section class="flex flex-col h-full">
        <h1 class="mb-5 text-xl font-heading md:text-2xl" :class="{'mb-3': codeRequested}">
            {{ codeRequested ? 'Change your password' : 'Request password change' }}
        </h1>

        <Form
            v-if="!codeRequested"
            class="flex flex-col flex-grow p-0 space-y-2"
            :validation-schema="emailFormValidation"
            @submit="requestCode"
        >
            <AiInput
                name="email"
                type="email"
                data-test="email"
                :initial-value="route.params['email']?.toString()"
                label="Email"
                :pre-icon="AccountOutline"
            />

            <template v-if="!codeRequested">
                <div class="flex-grow" />

                <AiAlert
                    v-if="!codeRequested"
                    :show-icon="false"
                    variant="info"
                    text="A confirmation code will be sent to your email address. You will need to enter this code to change your password."
                    class="my-2"
                />

                <div class="flex flex-row items-center">
                    <AiButton
                        light
                        class="mr-2"
                        data-test="iHaveACode-btn"
                        @click="userHasCode"
                    >
                        I have a code
                    </AiButton>
                    <div class="flex-grow" />
                    <AiButton
                        data-test="requestCode-btn"
                        primary
                        type="submit"
                    >
                        Request Code
                    </AiButton>
                </div>
            </template>
        </Form>
        <Form
            v-if="codeRequested"
            class="flex flex-col flex-grow h-full"
            :validation-schema="changePasswordFormValidation"
            @submit="submitChangePassword"
        >
            <div class="flex-grow h-full space-y-2">
                <AiInput
                    name="email"
                    type="email"
                    data-test="email"
                    label="Email"
                    :pre-icon="AccountOutline"
                />
                <AiInput
                    name="code"
                    type="text"
                    data-test="confirmation-code"
                    label="Confirmation code"
                    :pre-icon="CodeIcon"
                />
                <AiInput
                    name="newPassword"
                    type="password"
                    data-test="password"
                    label="New password"
                    :pre-icon="LockOutline"
                />
            </div>
            <div class="flex flex-row items-center">
                <AiButton
                    data-test="iNeedACode-btn"
                    light
                    @click="userNeedsCode"
                >
                    I need a code
                </AiButton>
                <div class="flex-grow" />
                <AiButton
                    data-test="changePassword-btn"
                    primary
                    :loading="changePasswordLoader"
                    type="submit"
                >
                    Change Password
                </AiButton>
            </div>
        </Form>
    </section>
</template>

<script setup lang="ts">
import { Form } from "vee-validate";
import { ref } from "vue";
import { useRoute } from "vue-router";
import { object, string } from "yup";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiInput from "@/components/AiInput/AiInput.vue";
import { IChangePassword,ICodeRequest } from "@/interfaces/auth/interfaces";
import { routeChange } from "@/router";
import { useAuthStore } from "@/store/auth";
import { useErrorStore } from "@/store/error";
import { emailValidation,passwordValidation } from "@/utils/forms/validation";
import { Snackbar } from "@/utils/snackbar";
import AccountOutline from "~icons/mdi/account-outline";
import CodeIcon from "~icons/mdi/key-outline";
import LockOutline from "~icons/mdi/lock-outline";


const emailFormValidation = object().shape({ email: emailValidation });

const changePasswordFormValidation = object().shape({
    email: emailValidation,
    code: string()
        .required("A confirmation code is required"),
    newPassword: passwordValidation
});

const errorStore = useErrorStore();
const authStore = useAuthStore();
const route = useRoute();
const changePasswordLoader = ref(false);
const codeRequested = ref(false);

/**
 * Methods
 */
const userHasCode = () => {
    codeRequested.value = true;
};

const userNeedsCode = () => {
    codeRequested.value = false;
};

const requestCode = async (values: unknown) => {

    if(codeRequested.value) {
        return;
    }

    const email = (values as ICodeRequest).email;

    try {
        await authStore.resetPassword(email);
    } catch (e) {
        errorStore.setError();

        return;
    }

    codeRequested.value = true;
};

const submitChangePassword = async (v: unknown): Promise<void> => {
    if(changePasswordLoader.value) {
        return;
    }

    changePasswordLoader.value = true;

    const values = v as IChangePassword;

    try {
        await authStore.updateForgottenPassword(values);
    } catch (e) {
        Snackbar.show({
            type: "error",
            title: "Error",
            text: "There was a problem changing your password. Please check your details and request a new code."
        }, 8_000);

        errorStore.setError();

        changePasswordLoader.value = false;
        codeRequested.value = false;

        return;
    }

    changePasswordLoader.value = false;

    Snackbar.success({
        title: "Password Changed",
        text: "You've updated your password. You can now log in using it."
    });

    routeChange.gotoLogin();
};
</script>
