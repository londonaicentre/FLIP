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
    <div class="flex flex-col w-full h-screen font-sans antialiased text-gray-700 dark:text-gray-300 bg-body dark:bg-dark-surface">
        <transition name="slidedown">
            <AiBanner v-if="details?.banner?.enabled" :message="details.banner.message" :link="details.banner.link" />
        </transition>
        <!-- Top nav -->
        <AiHeader :title="route.name?.toString() ?? ''" :current-page="route.fullPath" :is-dark="isDark" @toggle-dark="toggleDark">
            <AiUserDropdown
                :is-dark="isDark"
                :email-address="emailAddress"
                :display-name="displayName"
                :role="userRole"
                @sign-out="signOut"
                @toggle-dark-mode="toggleDark"
            />
        </AiHeader>

        <!-- Main Content (full-width — sidebar removed in favour of top nav) -->
        <AiErrorAlert v-if="errorStore.hasError" />

        <main class="flex w-full overflow-auto grow focus:outline-none bg-body dark:bg-dark-surface">
            <router-view v-slot="{ Component }">
                <DeploymentMode v-if="details.deploymentMode && !route.path.includes('/admin/')" />

                <Transition v-else-if="isProjectPage" name="fade" mode="out-in">
                    <AiLoader v-if="!hasProject" />
                    <div v-else class="w-full h-full">
                        <component :is="Component" class="w-full" @update-project="mutate" />
                        <CreateModelModal
                            :open="modalsStore.createModelOpen"
                            @close-modal="modalsStore.toggleCreateModel"
                        />
                    </div>
                </Transition>
                <template v-else>
                    <component :is="Component" />
                </template>
            </router-view>
        </main>
    </div>
</template>

<script setup lang="ts">
import { useDark, useToggle, whenever } from "@vueuse/core";
import useSWRV from "swrv";
import { fetcherFn, revalidateOptions } from "swrv/dist/types";
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import AiErrorAlert from "@/components/AiAlert/AiErrorAlert.vue";
import AiBanner from "@/components/AiBanner/AiBanner.vue";
import AiHeader from "@/components/AiHeader/AiHeader.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import AiUserDropdown from "@/components/AiUserDropdown/AiUserDropdown.vue";
import { usePermissions } from "@/composables/usePermissions";
import DeploymentMode from "@/pages/DeploymentMode.vue";
import CreateModelModal from "@/partials/models/CreateModelModal.vue";
import { getProject, IProject } from "@/services/project-service";
import { validateUser } from "@/services/user-service";
import { useAuthStore } from "@/store/auth";
import { useErrorStore } from "@/store/error";
import { useModalsStore } from "@/store/modals";
import { useProjectStore } from "@/store/project";
import { useSiteDetailsStore } from "@/store/siteDetailsStore";
import { useSiteSettings } from "@/store/siteSettingsStore";
import { Snackbar } from "@/utils/snackbar";

const projectStore = useProjectStore();
const modalsStore = useModalsStore();
const router = useRouter();
const route = useRoute();
const errorStore = useErrorStore();
const authStore = useAuthStore();
const details = useSiteDetailsStore();
const siteSettings = useSiteSettings();

const headerTitle = ref(route.name?.toString() ?? "");
const pageRoute = ref(route.fullPath?.toString() ?? "");
const emailAddress = authStore.user?.attributes?.email ?? "";

const { data: currentUserProfile } = useSWRV(
    () => emailAddress && `/users/${emailAddress}`,
    () => validateUser(emailAddress),
    {
        dedupingInterval: 60_000,
        revalidateOnFocus: false,
        shouldRetryOnError: false
    }
);

const displayName = computed(() => currentUserProfile.value?.name || emailAddress);

const { isAdmin, canCreateProjects } = usePermissions();
const userRole = computed(() => {
    if (isAdmin.value) return "Admin";
    if (canCreateProjects.value) return "Researcher";

    return "Viewer";
});

const hasProject = ref(false);
const isProjectPage = ref(false);

const isDark = useDark();
const toggleDark = useToggle(isDark);

watch(isDark, () => {
    siteSettings.toggleDarkMode(isDark.value);
}, { immediate: true });

const mutate = ref<(data?: fetcherFn<IProject> | undefined, opts?: revalidateOptions | undefined) => Promise<void>>();

// if route.params.projectId is falsy, don't fetch
const { data, mutate: update, error } = useSWRV(
    () => route.params.projectId && `/projects/${route.params.projectId}`,
    getProject,
    {
        refreshInterval: !window.Cypress ? 5_000 : 0,
        dedupingInterval: 5_000,
        revalidateOnFocus: false,
        shouldRetryOnError: true,
        errorRetryCount: 3
    }
);

mutate.value = update;

whenever(data, () => {
    projectStore.setProject(data.value);
    hasProject.value = true;
});

whenever(error, () => {
    hasProject.value = false;

    Snackbar.error({
        title: "Not found",
        text: "The requested project could not be found."
    });

    router.push({ path: "/" });
});

watch(() => route, () => {

    headerTitle.value = route.name?.toString() ?? "";
    pageRoute.value = route.fullPath.toString();

    if(route.fullPath.startsWith("/project/")) {
        isProjectPage.value = true;
    }
    else {
        hasProject.value = false;
        isProjectPage.value = false;
        projectStore.clearProject();
    }
}, {
    immediate: true,
    deep: true
});

const signOut = async () => {
    await authStore.signOut();
};
</script>
