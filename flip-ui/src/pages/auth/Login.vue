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
        <p class="mb-2 text-xs font-heading text-gray-500 dark:text-gray-300">
            FEDERATED LEARNING & INTEROPERABILITY PLATFORM
        </p>
        <h1 class="mb-5 text-xl font-heading md:text-3xl dark:text-gray-100">
            Sign in to <span class="text-primary-500 dark:text-primary-300 underline underline-offset-4">FLIP</span>
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
                autocomplete="username"
                :pre-icon="AccountOutline"
            />
            <AiInput
                name="password"
                type="password"
                data-test="password"
                label="Password"
                autocomplete="current-password"
                :pre-icon="LockOutline"
            >
                <template #labelRight>
                    <router-link
                        v-if="authStore.capabilities.forgotPassword"
                        to="/auth/change-password"
                        data-test="forgot-password-link"
                        class="text-sm text-right text-primary-500 hover:text-primary-700 dark:text-primary-300 dark:hover:text-primary-200 hover:underline"
                    >
                        Forgot password?
                    </router-link>
                    <!-- Keycloak owns the reset flow: hand over to its own page in a new tab. -->
                    <a
                        v-else
                        :href="externalResetUrl"
                        target="_blank"
                        rel="noopener noreferrer"
                        data-test="forgot-password-link"
                        class="text-sm text-right text-primary-500 hover:text-primary-700 dark:text-primary-300 dark:hover:text-primary-200 hover:underline"
                    >
                        Forgot password?
                    </a>
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
            >
                Log In
            </AiButton>
            <p class="text-sm text-center text-gray-600 dark:text-gray-300">
                Don't have an account?
                <button
                    type="button"
                    data-test="request-access-btn"
                    class="font-medium text-primary-500 hover:text-primary-700 dark:text-primary-300 dark:hover:text-primary-200 hover:underline"
                    @click="routeChange.accessRequest()"
                >
                    Request access
                </button>
            </p>
        </Form>
    </section>
</template>

<script setup lang="ts">
import { Form } from "vee-validate";
import { onBeforeMount, ref } from "vue";
import { object } from "yup";

import { keycloakResetCredentialsUrl } from "@/auth/keycloak-provider";
import { AccountActionRequiredError, SignInStep } from "@/auth/provider";
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

// Only a backend without an in-app reset flow (Keycloak) renders the
// external link, so this is only ever read when the Keycloak config exists.
const externalResetUrl = authStore.capabilities.forgotPassword ? "" : keycloakResetCredentialsUrl();

onBeforeMount(async () => {
    // Only redirect to /projects if the user has a real session. The
    // provider's `hasSession` answers false for a stale challenge-only
    // session — the previous `routeChange.viewProjects()` on any
    // non-throwing session read was what made "Back to log in" from
    // mid-challenge pages bounce straight back to the challenge page via
    // the router guard.
    try {
        if (await authStore.hasSession()) {
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

        // Route based on the next step returned by the provider. Challenge
        // pages drive their own follow-ups; once the challenge chain is
        // cleared, the MFA gate (via `needsMfaEnrolment`) decides whether to
        // send the user to the app or into post-auth enrolment.
        switch (authStore.signInStep) {
            case SignInStep.NEW_PASSWORD_REQUIRED:
                routeChange.newPassword();
                break;
            case SignInStep.TOTP_SETUP:
                routeChange.mfaSetup();
                break;
            case SignInStep.TOTP_CODE:
                routeChange.mfaVerify();
                break;
            default:
                if (authStore.needsMfaEnrolment && authStore.capabilities.totpEnrolment) {
                    routeChange.mfaSetup();
                } else {
                    routeChange.viewProjects();
                }
        }
    } catch (e) {
        if (e instanceof AccountActionRequiredError) {
            // The provider will not issue tokens until the user completes a
            // required action in its own UI (Keycloak: forced password
            // update, incomplete profile, ...). Point them there; the
            // notice stays up long enough to come back to.
            const actionUrl = e.actionUrl;
            Snackbar.warning({
                title: "Finish setting up your account",
                text: "Finish setting up your account in Keycloak, then sign in again.",
                actionText: "Open Keycloak",
                action: () => {
                    window.open(actionUrl, "_blank", "noopener,noreferrer");
                }
            }, 60_000);
        } else {
            Snackbar.show({
                type: "error",
                title: "Error",
                text: "There was a problem logging you in. Please check your details and try again."
            });
        }
    }

    loginLoader.value = false;
};
</script>

<route lang="yaml">
meta:
    layout: AuthLayout
</route>
