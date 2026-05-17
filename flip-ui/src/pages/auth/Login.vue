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

<!-- eslint-disable vue/multi-word-component-names -->
<template>
    <section class="flex flex-col h-full">
        <p class="mb-2 text-xs font-heading text-gray-500">
            FEDERATED LEARNING & INTEROPERABILITY PLATFORM
        </p>
        <h1
            class="mb-5 text-xl font-heading font-black md:text-3xl [-webkit-text-stroke:1px_currentColor]"
        >
            Sign in to <span class="text-primary-500 underline underline-offset-4">FLIP</span>
        </h1>
        <Form
            :validation-schema="schema"
            class="flex flex-col flex-grow p-0 space-y-4"
            @submit="submit"
        >
            <div class="grow" />
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
                    <router-link
                        to="/auth/change-password"
                        class="text-sm text-right text-primary-500 hover:text-primary-700 hover:underline"
                        tabindex="3"
                    >
                        Forgot password?
                    </router-link>
                </template>
            </AiInput>
            <div class="grow" />
            <AiButton
                primary
                data-test="login-btn"
                :loading="loginLoader"
                type="submit"
                block
                class="w-full [&_button]:py-3 [&_button]:rounded-lg"
                :input-props="{tabindex: 4}"
            >
                Log In
            </AiButton>
            <p class="text-sm text-center text-gray-600">
                Don't have an account?
                <button
                    type="button"
                    data-test="request-access-btn"
                    class="font-medium text-primary-500 hover:text-primary-700 hover:underline"
                    @click="routeChange.accessRequest()"
                >
                    Request access
                </button>
            </p>
        </Form>
    </section>
</template>

<script setup lang="ts">
import { fetchAuthSession } from "aws-amplify/auth";
import { Form } from "vee-validate";
import { onBeforeMount, ref } from "vue";
import { object } from "yup";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiInput from "@/components/AiInput/AiInput.vue";
import { routeChange } from "@/router";
import { useAuthStore } from "@/store/auth";
import { emailValidation, passwordValidation } from "@/utils/forms/validation";
import { Snackbar } from "@/utils/snackbar";
import AccountOutline from "~icons/mdi/account-outline";
import LockOutline from "~icons/mdi/lock-outline";

interface ILogin {
    email: string;
    password: string;
}

const authStore = useAuthStore();
const loginLoader = ref(false);

onBeforeMount(async () => {
    // Only redirect to /projects if the user has a real access token.
    // `fetchAuthSession` can return a session object (e.g. with a stale
    // challenge string) without tokens and without throwing, and the
    // previous `routeChange.viewProjects()` on any non-throw was what
    // made "Back to log in" from mid-challenge pages bounce straight
    // back to the challenge page via the router guard.
    try {
        const session = await fetchAuthSession();
        if (session.tokens?.accessToken) {
            routeChange.viewProjects();
        }
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

        // Route based on the next step returned by Cognito. Challenge pages
        // drive their own follow-ups; once the challenge chain is cleared,
        // the MFA gate (via `needsMfaEnrolment`) decides whether to send
        // the user to the app or into post-auth enrolment.
        switch (authStore.signInStep) {
            case "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED":
                routeChange.newPassword();
                break;
            case "CONTINUE_SIGN_IN_WITH_TOTP_SETUP":
                routeChange.mfaSetup();
                break;
            case "CONFIRM_SIGN_IN_WITH_TOTP_CODE":
                routeChange.mfaVerify();
                break;
            default:
                if (authStore.needsMfaEnrolment) {
                    routeChange.mfaSetup();
                } else {
                    routeChange.viewProjects();
                }
        }
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
