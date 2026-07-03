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
    <div class="bg-body dark:bg-dark-canvas">
        <div class="flex items-center justify-center h-screen">
            <div class="absolute top-6 left-8 z-10 flex items-center gap-4">
                <a
                    href="https://www.aicentre.co.uk/"
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label="AI Centre for Value Based Healthcare"
                >
                    <img
                        v-if="!isDark"
                        src="/images/aicentre-logo-transparent.webp"
                        alt="AI Centre for Value Based Healthcare"
                        class="h-20 w-auto"
                    >
                    <img
                        v-else
                        src="/images/aicentre-logo-transparent-dark.webp"
                        alt="AI Centre for Value Based Healthcare"
                        class="h-20 w-auto"
                    >
                </a>
                <a
                    href="https://londonaicentreflip.readthedocs.io/en/latest/"
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label="FLIP documentation"
                >
                    <img
                        src="/images/flip-logo.webp"
                        alt="FLIP"
                        class="h-12 w-auto"
                    >
                </a>
            </div>
            <div class="absolute top-0 right-0">
                <img src="@/assets/login/top-right.svg?url" alt="">
            </div>
            <div class="absolute bottom-0 left-0">
                <img src="@/assets/login/bottom-left.svg?url" alt="">
            </div>
            <div
                class="flex flex-row w-full md:max-w-md min-h-[417px] relative m-4"
            >
                <div class="flex flex-grow bg-white dark:bg-dark-surface py-4 px-8 border border-gray-200 dark:border-dark-border rounded-lg">
                    <div class="flex flex-col flex-grow">
                        <div class="flex pb-1">
                            <button
                                v-if="showBackToLogin"
                                type="button"
                                data-test="back-to-login"
                                class="inline-flex items-center text-sm"
                                @click="backToLogin"
                            >
                                <icon-mdi-chevron-left class="mr-1" />
                                Back to log in
                            </button>
                            <div class="flex-grow" />
                        </div>
                        <main class="flex flex-col flex-grow">
                            <router-view />
                        </main>
                    </div>
                </div>
            </div>
            <footer
                data-test="auth-footer-links"
                class="absolute bottom-4 left-1/2 -translate-x-1/2 flex flex-row items-center justify-center gap-6 text-xs font-heading text-gray-500 dark:text-gray-400 z-10"
            >
                <a
                    v-for="link in footerLinks"
                    :key="link.href"
                    :href="link.href"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="hover:text-gray-700 dark:hover:text-gray-200 hover:underline"
                >
                    {{ link.label }}
                </a>
            </footer>
        </div>
    </div>
</template>

<script setup lang="ts">
import { useDark } from "@vueuse/core";
import { signOut as amplifySignOut } from "aws-amplify/auth";
import { computed } from "vue";
import { useRoute } from "vue-router";

import AiErrorAlert from "@/components/AiAlert/AiErrorAlert.vue";
import { useAuthStore } from "@/store/auth";
import { useErrorStore } from "@/store/error";

const isDark = useDark();
const errorStore = useErrorStore();
const authStore = useAuthStore();
const route = useRoute();

const routeName = computed(() => route?.name);

const showBackToLogin = computed(() => routeName.value !== "auth-Login");

const footerLinks = [
    {
        label: "AI CENTRE FOR VALUE-BASED HEALTHCARE",
        href: "https://www.aicentre.co.uk/"
    },
    {
        label: "KING'S COLLEGE LONDON",
        href: "https://www.kcl.ac.uk/"
    },
    {
        label: "GUY'S & ST THOMAS' NHS FOUNDATION TRUST",
        href: "https://www.guysandstthomas.nhs.uk/"
    }
];

// Leave the current auth flow and land on /auth/login, whatever the
// current state is. A soft Vue Router push is not safe here: after a
// failed MFA attempt Amplify can leave in-memory state (and Pinia can
// leave `signInStep`) that the router guard or Login.vue's onBeforeMount
// will use to bounce the user straight back to the challenge page. A
// hard navigation (window.location.assign) tears the whole SPA down and
// brings it back up against the freshly-cleared localStorage, so there
// is nothing left to resurrect.
const backToLogin = (): void => {
    amplifySignOut().catch(() => { /* no-op: nothing to sign out of is fine */ });
    authStore.$reset();
    localStorage.clear();
    window.location.assign("/auth/login");
};
</script>
