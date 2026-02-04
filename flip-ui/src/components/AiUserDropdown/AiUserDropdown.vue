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
    <Menu as="div" class="relative inline-block text-left outline-none">
        <div>
            <MenuButton v-slot="{ open }" as="div" data-test="account-menu-btn">
                <div
                    class="group w-full transition cursor-pointer rounded-md px-3.5 py-2 text-sm text-left hover:bg-gray-100 dark:hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-100 dark:focus:ring-offset-gray-900 focus:ring-purple-500"
                    :class="{'ring-2 ring-primary-500 dark:ring-primary-400 rounded': open}"
                >
                    <span class="flex items-center justify-between w-full">
                        <span class="flex items-center justify-between min-w-0 space-x-3">
                            <div
                                class="flex items-center justify-center overflow-hidden bg-gray-100 dark:bg-gray-800 rounded-full w-7 h-7"
                            >
                                <icon-ph-user-square-duotone
                                    class="flex-shrink-0 w-6 h-6 text-gray-500 dark:text-gray-400 rounded-full"
                                />
                            </div>
                            <span class="flex-col flex-1 hidden min-w-0 md:flex">
                                <span class="font-semibold truncate select-none">
                                    {{ emailAddress }}
                                </span>
                            </span>
                            <icon-heroicons-outline-selector
                                class="flex-shrink-0 w-4 h-4 text-gray-400 group-hover:text-gray-500"
                                aria-hidden="true"
                            />
                        </span>
                    </span>
                </div>
            </MenuButton>
        </div>

        <transition
            enter-active-class="transition duration-100 ease-out"
            enter-from-class="transform scale-95 opacity-0"
            enter-to-class="transform scale-100 opacity-100"
            leave-active-class="transition duration-75 ease-in"
            leave-from-class="transform scale-100 opacity-100"
            leave-to-class="transform scale-95 opacity-0"
        >
            <MenuItems
                class="absolute z-20 min-w-[16rem] mt-2 origin-top-right bg-white dark:bg-gray-900 divide-y divide-gray-100 dark:ring-white/20 dark:divide-gray-700 rounded-md shadow-lg right-2 ring-1 ring-black ring-opacity-5 focus:outline-none"
            >
                <div class="px-4 py-3 md:hidden">
                    <p class="text-sm text-gray-500">
                        Signed in as:
                    </p>
                    <p
                        class="text-sm font-semibold text-gray-700 dark:text-gray-400 truncate"
                    >
                        {{ emailAddress }}
                    </p>
                </div>
                <div class="px-1 py-1">
                    <MenuItem v-slot="{ active }">
                        <button
                            :class="[
                                active ? 'bg-gray-100 dark:bg-gray-800' : 'text-gray-600 dark:text-gray-400',
                                'group flex rounded-md items-center w-full px-3 py-2 text-sm transition font-semibold',
                            ]"
                            data-test="sign-out-btn"
                            @click="emit('toggleDarkMode')"
                        >
                            <icon-ph-sun-duotone v-if="isDark" class="dark:group-hover:text-yellow-200 h-5 w-5 mr-3 text-gray-500 transition group-hover:text-gray-600" />
                            <icon-ph-moon-stars-duotone v-else class="h-5 w-5 mr-3 text-gray-500 transition group-hover:text-gray-600 dark:group-hover:text-gray-400" />
                            {{ isDark ? 'Light Mode' : "Dark Mode" }}
                        </button>
                    </MenuItem>
                </div>
                <div class="px-1 py-1">
                    <MenuItem v-slot="{ active }">
                        <button
                            :class="[
                                active ? 'bg-gray-100 dark:bg-gray-800' : 'text-gray-600 dark:text-gray-400',
                                'group flex rounded-md items-center w-full px-3 py-2 text-sm transition font-semibold',
                            ]"
                            data-test="change-password-btn"
                            @click="changePassword"
                        >
                            <icon-ph-lock-duotone
                                class="w-5 h-5 mr-3 text-gray-500 transition group-hover:text-gray-600 dark:group-hover:text-gray-400"
                                aria-hidden="true"
                            />
                            Change Password
                        </button>
                    </MenuItem>
                </div>
                <div class="px-1 py-1">
                    <MenuItem v-slot="{ active }">
                        <button
                            :class="[
                                active ? 'bg-gray-100 dark:bg-gray-800' : 'text-gray-600 dark:text-gray-400',
                                'group flex rounded-md items-center w-full px-3 py-2 text-sm transition font-semibold',
                            ]"
                            data-test="sign-out-btn"
                            @click="signOut"
                        >
                            <icon-mdi-logout
                                class="w-5 h-5 mr-3 text-gray-500 transition group-hover:text-gray-600 dark:group-hover:text-gray-400"
                                aria-hidden="true"
                            />
                            Sign Out
                        </button>
                    </MenuItem>
                </div>
                <p v-if="appVersion" class="px-4 py-1 text-xs text-right text-gray-500 truncate">
                    {{ appVersion }}
                </p>
            </MenuItems>
        </transition>
    </Menu>
</template>

<script lang="ts" setup>
import { Menu, MenuButton, MenuItem,MenuItems } from "@headlessui/vue";

import { routeChange } from "@/router";

interface IAiUserDropdownProps {
    emailAddress: string;
    isDark: boolean;
}

const props = withDefaults(
    defineProps<IAiUserDropdownProps>(), { emailAddress: "" }
);

const emit = defineEmits(["signOut", "toggleDarkMode"]);

const signOut = () => {
    emit("signOut");
};

const changePassword = () => {
    routeChange.changePassword(props.emailAddress);
};

const appVersion = window.RELEASE_VERSION;

</script>
