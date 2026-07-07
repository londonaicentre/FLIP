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

<!-- eslint-disable vue/multi-word-component-names -->
<route lang="yaml">
name: Admin
redirect: /admin/users # Can be set to any page within the "admin" section
</route>

<template>
    <div class="flex flex-col w-full h-full">
        <!-- Section nav: standalone chips at every breakpoint (replaces the old
             xl-only sidebar + mobile <select>). Each chip squashes independently
             and the row scrolls horizontally on phone widths. -->
        <nav
            aria-label="Admin sections"
            data-test="admin-tabs"
            class="flex items-center gap-2 shrink-0 px-4 md:px-8 py-3 overflow-x-auto border-b border-gray-200 dark:border-dark-border"
        >
            <router-link
                v-for="item in subNavigation"
                :key="item.name"
                :to="item.href"
                :aria-current="item.current ? 'page' : undefined"
                class="flex items-center gap-1.5 px-3 py-1.5 rounded-full border text-sm font-semibold whitespace-nowrap transition-colors"
                :class="item.current
                    ? ['bg-primary-100 border-transparent text-primary-800',
                       'dark:bg-primary-900/40 dark:text-primary-100']
                    : ['text-gray-500 hover:text-gray-800 dark:text-gray-300 dark:hover:text-gray-200',
                       'bg-white border-gray-200 hover:border-gray-300 dark:bg-dark-canvas dark:border-dark-border']"
            >
                <component :is="item.icon" class="w-4 h-4 shrink-0" aria-hidden="true" />
                {{ item.name }}
            </router-link>
        </nav>
        <!-- Subpages own their gutters (Users runs edge-to-edge); this wrapper owns the scroll. -->
        <div data-test="admin-content" class="flex flex-col flex-1 min-w-0 overflow-y-auto">
            <router-view />
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed, onBeforeMount } from "vue";
import { useRouter } from "vue-router";

import { routeChange } from "@/router";
import { useAuthStore } from "@/store/auth";
import DeploymentIcon from "~icons/ph/download-duotone";
import BannerIcon from "~icons/ph/flag-banner-duotone";
import AccessRequestIcon from "~icons/ph/user-plus-duotone";
import UsersIcon from "~icons/ph/users-three-duotone";

const router = useRouter();
const authStore = useAuthStore();

onBeforeMount(() => {
    if(authStore.hasPermissions(["CanAccessAdminPanel"])) {
        return;
    }

    routeChange.viewProjects();
});

const subNavigation = computed(() => [
    {
        name: "Users",
        icon: UsersIcon,
        current: router.currentRoute.value.fullPath === "/admin/users",
        href: "/admin/users"
    },
    {
        name: "Access Requests",
        icon: AccessRequestIcon,
        current: router.currentRoute.value.fullPath === "/admin/access-requests",
        href: "/admin/access-requests"
    },
    {
        name: "Banner",
        icon: BannerIcon,
        current: router.currentRoute.value.fullPath === "/admin/banner",
        href: "/admin/banner"
    },
    {
        name: "Deployments",
        icon: DeploymentIcon,
        current: router.currentRoute.value.fullPath === "/admin/deployments",
        href: "/admin/deployments"
    }
]);
</script>
