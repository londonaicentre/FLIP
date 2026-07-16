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
    <AiErrorAlert v-if="errorStore.hasError" class="absolute z-10" />
    <div
        class="bg-body relative dark:bg-dark-canvas flex justify-center items-center h-dvh w-screen
            pt-[max(1rem,env(safe-area-inset-top))] pb-[max(1rem,env(safe-area-inset-bottom))]
            pl-[max(1rem,env(safe-area-inset-left))] pr-[max(1rem,env(safe-area-inset-right))]"
    >
        <div class="absolute bottom-0 left-0">
            <img src="@/assets/login/bottom-left.svg?url">
        </div>
        <div class="absolute top-0 right-0">
            <img src="@/assets/login/top-right.svg?url">
        </div>
        <div
            class="p-6 bg-white flex dark:bg-dark-surface w-full md:min-w-[760px] md:max-w-[800px] max-w-[75vh] min-h-[75vh] max-h-full shadow-xl rounded-md relative overflow-hidden"
        >
            <div class="flex flex-col gap-2 w-full relative">
                <img
                    v-if="!isDark"
                    src="/images/aicentre-logo-transparent.webp"
                    alt="AI Centre for Value Based Healthcare"
                    class="h-[70px] w-auto absolute top-0 right-0 p-0"
                >
                <img
                    v-else
                    src="/images/aicentre-logo-transparent-dark.webp"
                    alt="AI Centre for Value Based Healthcare"
                    class="h-[70px] w-auto absolute top-0 right-0 p-0"
                >
                <router-view />
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { useDark } from "@vueuse/core";

import AiErrorAlert from "@/components/AiAlert/AiErrorAlert.vue";
import { useErrorStore } from "@/store/error";

const isDark = useDark();
const errorStore = useErrorStore();
</script>
