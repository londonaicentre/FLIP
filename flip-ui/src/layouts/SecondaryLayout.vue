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

<template>
    <AiErrorAlert v-if="errorStore.hasError" class="absolute z-10" />
    <div class="bg-body relative dark:bg-gray-900 flex justify-center items-center h-screen w-screen">
        <div class="absolute bottom-0 left-0">
            <img src="@/assets/login/bottom-left.svg?url">
        </div>
        <div class="absolute top-0 right-0">
            <img src="@/assets/login/top-right.svg?url">
        </div>
        <div
            class="p-6 bg-white flex dark:bg-gray-800 w-full md:min-w-[760px] md:max-w-[800px] max-w-[75vh] min-h-[75vh] max-h-[95vh] shadow-xl rounded-md relative overflow-hidden m-4"
        >
            <div class="flex flex-col gap-2 w-full relative">
                <logo v-if="!isDark" class="h-[70px] absolute top-0 right-0 p-0" />
                <logoDark v-else class="h-[70px] absolute top-0 right-0 p-0" />
                <router-view />
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { useDark } from "@vueuse/core";

import logo from "@/assets/logo.svg?component";
import logoDark from "@/assets/logo_dark.svg?component";
import AiErrorAlert from "@/components/AiAlert/AiErrorAlert.vue";
import { useErrorStore } from "@/store/error";

const isDark = useDark();
const errorStore = useErrorStore();
</script>
