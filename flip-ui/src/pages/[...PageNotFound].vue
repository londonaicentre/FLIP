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
    name: Page Not Found
</route>

<template>
    <div class="flex flex-col items-center justify-center flex-1 px-10 text-center">
        <div
            class="flex items-center justify-center w-16 h-16 rounded-full bg-primary-100 ring-1 ring-primary-200
            dark:bg-dark-raised dark:ring-dark-border"
        >
            <icon-ph-signpost-duotone
                class="w-[30px] h-[30px] text-primary-500 dark:text-primary-200"
                data-test="badge-icon"
            />
        </div>
        <p
            class="mt-5 text-[11px] font-black uppercase tracking-[0.06em] text-primary-600/70 dark:text-primary-300"
            data-test="eyebrow"
        >
            Error 404
        </p>
        <h1
            class="mt-2.5 text-[28px] font-semibold font-heading leading-[1.2] tracking-[-0.01em]
            text-gray-700 dark:text-gray-100"
        >
            We can't find the page you have requested.
        </h1>
        <p
            class="mt-3 text-sm font-medium leading-[1.55] text-gray-500 dark:text-gray-300 max-w-[520px]"
            data-test="subtitle"
        >
            Check the address and try again. If the issue persists, please contact the service desk.
        </p>
        <div class="flex flex-wrap items-center justify-center gap-3 mt-7">
            <AiButton primary link="/projects" data-test="home-button">
                View Projects
            </AiButton>
            <AiButton v-if="canGoBack" data-test="back-button" @click="routeChange.back()">
                Go back
            </AiButton>
        </div>
        <a
            href="mailto:aicentreflip@gmail.com"
            class="mt-5 text-[13px] font-semibold transition-colors text-primary-500 hover:text-primary-400
            dark:text-primary-200 dark:hover:text-primary-100"
            data-test="service-desk-link"
        >
            Contact the service desk
        </a>
    </div>
</template>

<script setup lang="ts">
import AiButton from "@/components/AiButton/AiButton.vue";
import { routeChange } from "@/router";

// vue-router records the previous in-app route at history.state.back; on a direct deep link to a bad
// URL there is none, and "Go back" would leave the app — hide the button instead (design handoff).
const canGoBack = !!window.history.state?.back;
</script>
