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

<route lang="yaml">
    name: AccessRequest
    meta:
        layout: SecondaryLayout
</route>

<template>
    <div class="flex">
        <router-link to="/auth/login" class="inline-flex items-center text-sm" data-test="back-to-login-btn">
            <icon-mdi-chevron-left class="mr-1" />
            Back to log in
        </router-link>
    </div>
    <div class="h-full w-full flex flex-col">
        <h1
            class="mb-5 text-xl font-heading md:text-2xl"
        >
            Request Access
        </h1>
        <Form
            v-slot="{ errors }"
            class="flex flex-col gap-4 h-full"
            :validation-schema="schema"
            @submit="submit"
        >
            <AiInput
                name="email"
                type="email"
                label="Email address"
                data-test="email-input"
                required
                :error="errors.message"
                :input-props="{tabindex: 1}"
            />
            <AiInput
                name="fullName"
                type="text"
                data-test="full-name-input"
                label="Full Name"
                required
                :error="errors.message"
                :input-props="{tabindex: 2}"
            />
            <AiTextArea
                name="reasonForAccess"
                type="text"
                data-test="reason-for-access-textarea"
                label="Reason for access"
                :error="errors.message"
                :input-props="{tabindex: 3}"
                required
            />
            <AiButton
                primary
                data-test="submit-access-request-btn"
                class="w-full mt-auto"
                type="submit"
                block
                :input-props="{tabindex: 4}"
                :loading="isFormSubmitting"
            >
                Submit your request
            </AiButton>
        </Form>
    </div>
</template>
<script setup lang="ts">
import { Form } from "vee-validate";
import { ref } from "vue";
import * as yup from "yup";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiInput from "@/components/AiInput/AiInput.vue";
import AiTextArea from "@/components/AiTextArea/AiTextArea.vue";
import { routeChange } from "@/router";
import { IAccessRequest, submitAccessRequest } from "@/services/user-service";
import { Snackbar } from "@/utils/snackbar";

const schema = yup.object({
    email: yup
        .string()
        .email("Email address must be in a valid format")
        .required("Email address is required"),
    fullName: yup
        .string()
        .required("Your full name is required"),
    reasonForAccess: yup
        .string()
        .required("Reason for access is required")
});

const isFormSubmitting = ref(false);

const submit = async (formData: unknown) => {
    if(!formData) {
        return;
    }

    isFormSubmitting.value = true;

    await submitAccessRequest(formData as IAccessRequest)
        .then(() => {
            Snackbar.success({
                title: "Success!",
                text: "Access request has been successfully submitted!"
            }, 30_000);

            routeChange.gotoLogin();
        })
        .catch(error => {
            console.error((error as Error).message);

            Snackbar.error({
                title: "Error",
                text: "Failed to submit access request. Please try again later."
            }, 30_000);
        });

    isFormSubmitting.value = false;
};
</script>
