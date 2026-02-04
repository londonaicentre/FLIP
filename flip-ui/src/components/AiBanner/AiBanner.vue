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
    <div v-if="showBanner" class="w-full" data-test="site-banner">
        <div class="mx-auto">
            <div class="p-2 transition bg-primary-500 sm:p-3">
                <div class="flex flex-wrap items-center justify-between mx-auto max-w-screen-2xl">
                    <span class="w-auto p-2 rounded-lg bg-primary-700">
                        <icon-ph-megaphone-duotone class="w-6 h-6 text-white" aria-hidden="true" />
                    </span>
                    <div class="flex items-center flex-1">
                        <p class="ml-3 font-medium text-white" data-test="banner-message" v-text="message" />
                    </div>
                    <div class="flex items-center flex-shrink-0 order-3 w-full mt-2 sm:order-2 sm:mt-0 sm:w-auto">
                        <a
                            v-if="link"
                            :href="link"
                            target="_blank"
                            data-test="banner-link"
                            class="flex items-center justify-center px-4 py-2 text-white hover:text-primary-200 grow"
                        >
                            Learn more
                        </a>
                        <div v-tippy="{placement: 'bottom-end'}" class="w-6 h-5 cursor-pointer" content="Close for this session" @click="close">
                            <icon-mdi-close class="w-5 h-5 text-white transition rounded hover:text-primary-200" />
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { directive as vTippy } from "vue-tippy";

interface IBannerProps {
    message: string;
    link?: string;
}

withDefaults(
    defineProps<IBannerProps>(),
    { link: undefined }
);

const showBanner = ref(true);

const close = () => {
    showBanner.value = false;
};
</script>
