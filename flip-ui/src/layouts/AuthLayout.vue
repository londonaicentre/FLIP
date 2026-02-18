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

﻿<template>
    <AiErrorAlert v-if="errorStore.hasError" class="absolute z-10" />
    <div class="bg-body dark:bg-gray-900">
        <div class="flex items-center justify-center h-screen">
            <div class="absolute top-0 right-0">
                <img src="@/assets/login/top-right.svg?url">
            </div>
            <div class="absolute bottom-0 left-0">
                <img src="@/assets/login/bottom-left.svg?url">
            </div>
            <div
                class="p-2 flex flex-row bg-white dark:bg-gray-800 w-full md:min-w-[760px] md:max-w-[800px] min-h-[417px] shadow-xl rounded-md relative m-4"
            >
                <div class="flex-shrink-0 hidden max-w-sm md:flex items-center bg-white justify-center grow rounded-lg">
                    <LoginBranding />
                </div>

                <div class="flex flex-grow p-2 md:pl-4">
                    <div class="flex flex-col flex-grow">
                        <div class="flex pb-1">
                            <template v-if="routeName !== 'auth-Login'">
                                <router-link to="/auth/login" class="inline-flex items-center text-sm">
                                    <icon-mdi-chevron-left class="mr-1" />
                                    Back to log in
                                </router-link>
                            </template>
                            <div class="flex-grow" />
                            <img src="/images/nhs-logo.png" alt="NHS logo" class="h-[40px] rounded">
                        </div>
                        <router-view />
                    </div>
                </div>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";

import AiErrorAlert from "@/components/AiAlert/AiErrorAlert.vue";
import LoginBranding from "@/partials/auth/LoginBranding.vue";
import { useErrorStore } from "@/store/error";

const errorStore = useErrorStore();
const route = useRoute();

const routeName = computed(() => route?.name);
</script>
