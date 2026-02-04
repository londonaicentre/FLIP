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

<!-- eslint-disable vue/multi-word-component-names -->
<template>
    <section class="flex flex-col h-full">
        <h1
            class="mb-5 text-xl font-heading md:text-2xl"
        >
            Log into your account
        </h1>
        <Form
            :validation-schema="schema"
            class="flex flex-col flex-grow p-0 space-y-4"
            @submit="submit"
        >
            <AiInput
                name="email"
                type="email"
                data-test="username"
                label="Email"
                :pre-icon="AccountOutline"
                :input-props="{tabindex: 1}"
            />
            <AiInput
                name="password"
                type="password"
                data-test="password"
                label="Password"
                :pre-icon="LockOutline"
                :input-props="{tabindex: 2}"
            >
                <template #labelRight>
                    <router-link to="/auth/change-password" class="text-sm text-right" tabindex="3">
                        Forgot password?
                    </router-link>
                </template>
            </AiInput>
            <div class="flex-grow" />
            <div class="flex flex-row items-center gap-2">
                <AiButton
                    block
                    clear
                    class="w-full"
                    data-test="request-access-btn"
                    @click="routeChange.accessRequest()"
                >
                    Request access
                </AiButton>
                <AiButton
                    primary
                    data-test="login-btn"
                    :loading="loginLoader"
                    type="submit"
                    block
                    class="w-full"
                    :input-props="{tabindex: 4}"
                >
                    Log In
                </AiButton>
            </div>
        </Form>
    </section>
</template>

<script setup lang="ts">
import { Form } from "vee-validate";
import { onBeforeMount, ref } from "vue";
import { object } from "yup";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiInput from "@/components/AiInput/AiInput.vue";
import { routeChange } from "@/router";
import { useAuthStore } from "@/store/auth";
import { emailValidation, passwordValidation } from "@/utils/forms/validation";
import { Snackbar } from "@/utils/snackbar";
import { fetchAuthSession } from "aws-amplify/auth";
import AccountOutline from "~icons/mdi/account-outline";
import LockOutline from "~icons/mdi/lock-outline";

interface ILogin {
    email: string;
    password: string;
}

const authStore = useAuthStore();
const loginLoader = ref(false);

onBeforeMount(async () => {
    try {
        await fetchAuthSession();
        routeChange.viewProjects();  // user already logged in
    } catch {
        // not logged in → stay on login page
    }
});

const schema = object().shape({
    email: emailValidation,
    password: passwordValidation
});

/*
 * Methods
 */

const submit = async (v: unknown): Promise<void> => {
    const values = v as ILogin;

    loginLoader.value = true;

    try {
        await authStore.signIn({
            username: values.email,
            password: values.password
        });

        routeChange.viewProjects();
    } catch (e) {
        Snackbar.show({
            type: "error",
            title: "Error",
            text: "There was a problem logging you in. Please check your details and try again."
        });
    }

    loginLoader.value = false;
};
</script>

<route lang="yaml">
meta:
    layout: AuthLayout
</route>
