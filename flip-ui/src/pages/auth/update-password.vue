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

<route lang="yaml">
meta:
    layout: AuthLayout
</route>

<template>
    <section class="flex flex-col h-full">
        <h1 class="mb-3 text-xl font-heading md:text-2xl">
            Update Password
        </h1>
        <p class="mb-4 text-sm">
            Enter your current password and choose a new one. After
            updating, you will be signed out and need to log in with
            your new password.
        </p>
        <Form
            :validation-schema="schema"
            class="flex flex-col flex-grow p-0 space-y-4"
            @submit="submit"
        >
            <AiInput
                name="currentPassword"
                type="password"
                data-test="current-password"
                label="Current password"
                :pre-icon="LockOutline"
            />
            <AiInput
                name="newPassword"
                type="password"
                data-test="new-password"
                label="New password"
                :pre-icon="LockOutline"
            />
            <AiInput
                name="confirmPassword"
                type="password"
                data-test="confirm-password"
                label="Confirm new password"
                :pre-icon="LockOutline"
            />
            <div class="flex-grow" />
            <div class="flex flex-row">
                <AiButton
                    data-test="cancel-btn"
                    light
                    class="mr-2"
                    @click="cancel"
                >
                    Cancel
                </AiButton>
                <div class="flex-grow" />
                <AiButton
                    primary
                    data-test="update-password-btn"
                    :loading="loading"
                    type="submit"
                >
                    Update Password
                </AiButton>
            </div>
        </Form>
    </section>
</template>

<script setup lang="ts">
import { Form } from "vee-validate";
import { ref } from "vue";
import { object, ref as yupRef, string } from "yup";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiInput from "@/components/AiInput/AiInput.vue";
import { routeChange } from "@/router";
import { useAuthStore } from "@/store/auth";
import { passwordValidation } from "@/utils/forms/validation";
import { Snackbar } from "@/utils/snackbar";
import LockOutline from "~icons/mdi/lock-outline";

const authStore = useAuthStore();
const loading = ref(false);

interface IUpdatePasswordForm {
    currentPassword: string;
    newPassword: string;
    confirmPassword: string;
}

const schema = object().shape({
    currentPassword: string()
        .required("Current password is required"),
    newPassword: passwordValidation,
    confirmPassword: string()
        .required("Please confirm your new password")
        .oneOf([yupRef("newPassword")], "Passwords must match"),
});

const cancel = () => {
    routeChange.viewProjects();
};

const submit = async (v: unknown): Promise<void> => {
    const { currentPassword, newPassword } = v as IUpdatePasswordForm;

    if (loading.value) {
        return;
    }

    loading.value = true;

    try {
        await authStore.updatePassword({ currentPassword, newPassword });
        // `updatePassword` calls signOut({global: true}) on success,
        // so we only reach here if signOut throws.
        Snackbar.success({
            title: "Password changed",
            text: "Your password has been updated. Please sign in again.",
        });
        routeChange.gotoLogin();
    } catch (e) {
        console.error("Password update failed:", e);
        const err = e as { name?: string; message?: string };
        const isWrongPassword =
            err.name === "NotAuthorizedException" ||
            /wrong.*password|incorrect.*password/i.test(err.message ?? "");

        if (isWrongPassword) {
            Snackbar.error({
                title: "Invalid current password",
                text: "The current password you entered is incorrect. Please try again.",
            });
        } else if (err.name === "InvalidPasswordException") {
            Snackbar.error({
                title: "Invalid new password",
                text: "The new password does not meet the requirements. Please choose a different one.",
            });
        } else {
            Snackbar.error({
                title: "Password update failed",
                text: err.message ?? "Something went wrong. Please try again.",
            });
        }
    } finally {
        loading.value = false;
    }
};
</script>
