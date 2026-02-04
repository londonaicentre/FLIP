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
    <div class="relative flex flex-col w-full h-full overflow-y-auto grow">
        <transition name="fade" mode="out-in">
            <div v-if="!details.banner" class="py-36">
                <AiLoader />
            </div>
            <div v-else class="flex flex-col space-y-4 grow">
                <Form v-slot="{values}" :validation-schema="schema" @submit="updateBanner">
                    <div class="relative p-4 overflow-hidden transition">
                        <div
                            class="relative pb-4"
                        >
                            <icon-ph-flag-banner-duotone
                                class="w-16 h-16 mb-8 transition"
                                :class="[details.banner.enabled ? 'text-primary-600 dark:text-green-400' : 'text-gray-300']"
                            />
                            <h3 class="mt-2 text-3xl font-semibold font-heading">
                                Site Banner is <span
                                    class="font-black underline uppercase transition decoration-4 decoration-solid underline-offset-8"
                                    :class="[details.banner.enabled ? 'decoration-primary-600 dark:decoration-green-400' : 'decoration-transparent']"
                                >
                                    {{ details.banner.enabled ? 'Enabled' : 'Disabled' }}
                                </span>
                            </h3>
                            <p class="max-w-2xl my-6 text-gray-400">
                                Enabling the <strong class="font-bold">site banner</strong> will show a message on every
                                page to all users across the platform.
                                You can edit and preview the banner below.
                            </p>
                            <AiButton primary :loading="loadingButton" @click="confirm">
                                {{ details.banner.enabled ? 'Disable' : 'Enable' }} Site Banner
                            </AiButton>
                        </div>
                    </div>
                    <div class="relative p-4 bg-gray-200 dark:bg-gray-600">
                        <div class="overflow-hidden border border-gray-300 rounded-lg shadow-lg dark:border-gray-700 bg-gray-50 dark:bg-gray-900">
                            <div class="w-full">
                                <div class="p-2 transition bg-primary-500 sm:p-3">
                                    <div class="flex flex-wrap items-center justify-between mx-auto max-w-7xl">
                                        <div class="flex items-center flex-1">
                                            <span class="flex p-2 rounded-lg bg-primary-700">
                                                <icon-ph-megaphone-duotone
                                                    class="w-6 h-6 text-white"
                                                    aria-hidden="true"
                                                />
                                            </span>
                                            <p class="ml-3 font-medium text-white" v-text="values.message" />
                                        </div>
                                        <div
                                            v-if="values.link"
                                            class="flex-shrink-0 order-3 w-full mt-2 sm:order-2 sm:mt-0 sm:w-auto"
                                        >
                                            <a
                                                :href="values.link"
                                                target="_blank"
                                                class="flex items-center justify-center px-4 py-2 text-white group"
                                            >
                                                Learn more
                                                <icon-mdi-arrow-right
                                                    class="ml-2 transition group-hover:translate-x-1"
                                                />
                                            </a>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <div class="relative h-full px-4 py-5 sm:p-6">
                                <div class="space-y-4">
                                    <div class="flex flex-col flex-grow p-0 space-y-4">
                                        <AiTextArea
                                            label="Banner Message"
                                            name="message"
                                            hint="This message will be displayed in the main part of the banner"
                                            :initial-value="`${details.banner.message}`"
                                            data-test="edit-banner-message"
                                        />
                                        <transition name="fade" mode="out-in">
                                            <AiInput
                                                v-if="addBannerLink"
                                                label="Banner Link"
                                                name="link"
                                                hint="The user will be redirected to this link when they click 'Learn more'"
                                                :initial-value="details.banner.link ? `${details.banner.link}` : 'update-me.com'"
                                                data-test="edit-banner-link"
                                            >
                                                <template #inputButton>
                                                    <AiButton class="ml-2" @click="toggleBannerLink">
                                                        Remove banner link
                                                    </AiButton>
                                                </template>
                                            </AiInput>
                                            <div
                                                v-else-if="!details.banner.link"
                                                class="flex flex-col items-start gap-2"
                                            >
                                                <AiButton @click="toggleBannerLink">
                                                    Add a banner link
                                                </AiButton>
                                                <div class="flex flex-row items-center w-full gap-2">
                                                    <AiSkeleton class="h-12 grow" />
                                                </div>
                                            </div>
                                        </transition>
                                        <AiButton light type="submit" :loading="loadingButton">
                                            Update Banner
                                        </AiButton>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </Form>
            </div>
        </transition>
    </div>
    <AiConfirmModal
        :dialog="confirmDialog"
        :confirmation-text="confirmationText"
        close-button-text="Cancel"
        title="Enable site banner?"
        continue-button-text="Enable site banner"
        :continue-action="toggleBanner"
        :submitting="loadingButton"
        @close-modal="close"
    />
</template>

<script setup lang="ts">
import { Form } from "vee-validate";
import { ref } from "vue";
import { object, string } from "yup";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiInput from "@/components/AiInput/AiInput.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import AiSkeleton from "@/components/AiSkeleton/AiSkeleton.vue";
import AiTextArea from "@/components/AiTextArea/AiTextArea.vue";
import { ISiteBanner, useSiteDetailsStore } from "@/store/siteDetailsStore";

const details = useSiteDetailsStore();
const loadingButton = ref(false);
const confirmDialog = ref(false);
const addBannerLink = ref(!!details.banner?.link);

const schema = object().shape({
    message: string().required(),
    link: string().notRequired().url("Please enter a valid URL, prefixed with 'http(s)://'"),
    enabled: string().optional()
});

const confirmationText = `
    Are you sure you want to enable the <strong>site banner</strong>?
    <p class='mt-2'>
        This will show across all pages for every user. Make to check the preview before enabling it.
    </p>`;

const confirm = async () => {
    if(details.banner?.enabled) {
        toggleBanner();

        return;
    }

    confirmDialog.value = true;
};

const close = () => {
    confirmDialog.value = false;
};

const updateBanner = async (v: unknown) => {
    const banner = v as ISiteBanner;

    if (loadingButton.value) {
        return;
    }

    loadingButton.value = true;

    if (!details.banner) {
        return;
    }

    await useSiteDetailsStore().updateBanner({
        ...banner,
        enabled: details.banner.enabled
    });

    loadingButton.value = false;
};

const toggleBanner = async () => {

    if (loadingButton.value) {
        return;
    }

    loadingButton.value = true;


    if(!details.banner) {
        return;
    }

    await useSiteDetailsStore().updateBanner({
        ...details.banner,
        enabled: !details.banner.enabled
    });

    close();
    loadingButton.value = false;
};

const toggleBannerLink = () => {
    addBannerLink.value = !addBannerLink.value;
};

</script>
