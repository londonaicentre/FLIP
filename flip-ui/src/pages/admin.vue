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
    <div class="flex w-full h-full">
        <div class="flex flex-col flex-1 min-w-0 overflow-hidden">
            <main class="flex flex-1 overflow-hidden">
                <div class="flex flex-col flex-1 overflow-y-auto xl:overflow-hidden">
                    <nav aria-label="Breadcrumb" class="bg-white border-b border-blue-gray-200 xl:hidden">
                        <div class="flex items-start w-full px-4 py-3">
                            <div class="w-full xl:hidden">
                                <label for="nav-tabs" class="sr-only">Select a tab</label>
                                <select
                                    id="nav-tabs"
                                    class="block w-full text-base font-medium text-gray-900 border-gray-300 rounded-md shadow-sm focus:border-primary-500 focus:ring-primary-500"
                                    @change="(event) => updateRoute(event)"
                                >
                                    <option
                                        v-for="tab in subNavigation"
                                        :key="tab.name"
                                        :selected="tab.current"
                                        :value="tab.href"
                                    >
                                        {{ tab.name }}
                                    </option>
                                </select>
                            </div>
                        </div>
                    </nav>

                    <div class="flex flex-1 xl:overflow-hidden">
                        <nav
                            aria-label="Sections"
                            class="flex-shrink-0 hidden p-4 pr-0 w-60 border-blue-gray-200 xl:flex xl:flex-col"
                        >
                            <aside class="px-2 py-6 sm:px-6 lg:py-0 lg:px-0 lg:col-span-3">
                                <nav class="space-y-1">
                                    <router-link
                                        v-for="item in subNavigation"
                                        :key="item.name"
                                        :to="item.href"
                                        :class="[item.current
                                                     ? 'bg-white dark:bg-gray-900 text-primary-600 dark:text-primary-100 hover:bg-white border-gray-300 dark:hover:bg-gray-900 dark:border-gray-700'
                                                     : 'border-transparent text-gray-700 hover:text-gray-900 hover:bg-white dark:hover:bg-gray-900 dark:text-gray-400 dark:hover:text-gray-300',
                                                 'transition group rounded-md px-3 py-2 flex items-center text-sm font-medium user-select-none border']"
                                        :aria-current="item.current ? 'page' : undefined"
                                    >
                                        <component
                                            :is="item.icon"
                                            :class="[item.current ? 'text-primary-500 dark:text-primary-100' : 'text-gray-400 group-hover:text-gray-500 dark:group-hover:text-gray-300', 'transition flex-shrink-0 -ml-1 mr-3 h-6 w-6']"
                                            aria-hidden="true"
                                        />
                                        <div class="flex flex-row items-center w-full">
                                            <div class="font-semibold truncate grow">
                                                {{ item.name }}
                                            </div>
                                            <icon-mdi-chevron-right
                                                :class="[item.current ? 'text-primary-500 dark:text-primary-100' : 'text-gray-400 group-hover:text-gray-500 dark:group-hover:text-gray-300', 'transition']"
                                            />
                                        </div>
                                    </router-link>
                                </nav>
                            </aside>
                        </nav>
                        <div class="p-4 mx-auto grow xl:overflow-y-auto">
                            <router-view />
                        </div>
                    </div>
                </div>
            </main>
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

const updateRoute = (event: Event) => {
    router.push({ path: (event.target as HTMLButtonElement).value });
};
</script>
