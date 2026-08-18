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
    <Menu as="div" class="relative inline-block text-left outline-none">
        <div>
            <MenuButton
                v-slot="{ open }"
                class="block w-full text-left rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-gray-100 dark:focus-visible:ring-offset-dark-canvas focus-visible:ring-purple-500"
                data-test="account-menu-btn"
            >
                <div
                    class="group w-full transition cursor-pointer rounded-md px-3.5 py-2 text-sm text-left hover:bg-gray-100 dark:hover:bg-dark-surface"
                    :class="{'ring-2 ring-primary-500 dark:ring-primary-400 rounded': open}"
                >
                    <span class="flex items-center justify-between w-full">
                        <span class="flex items-center justify-between min-w-0 space-x-3">
                            <icon-ph-user-circle
                                class="flex-shrink-0 w-7 h-7 text-gray-500 dark:text-gray-300"
                            />
                            <span class="flex-col flex-1 hidden min-w-0 md:flex">
                                <span class="font-semibold truncate select-none">
                                    {{ displayName }}{{ role ? ` (${role})` : '' }}
                                </span>
                            </span>
                            <icon-ph-caret-down
                                class="flex-shrink-0 w-4 h-4 text-gray-400 dark:text-gray-300 group-hover:text-gray-500 dark:group-hover:text-gray-200"
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
                class="absolute z-20 min-w-[16rem] mt-2 origin-top-right bg-white dark:bg-dark-canvas divide-y divide-gray-100 dark:ring-white/20 dark:divide-dark-border rounded-md shadow-lg right-2 ring-1 ring-black ring-opacity-5 focus:outline-none"
            >
                <div class="px-4 py-3 md:hidden">
                    <p class="text-sm text-gray-500 dark:text-gray-300">
                        Signed in as:
                    </p>
                    <p
                        class="text-sm font-semibold text-gray-700 dark:text-gray-300 truncate"
                    >
                        {{ displayName }}{{ role ? ` (${role})` : '' }}
                    </p>
                </div>
                <div class="px-1 py-1">
                    <MenuItem v-slot="{ active }">
                        <button
                            :class="[
                                active ? 'bg-gray-100 dark:bg-dark-surface' : 'text-gray-600 dark:text-gray-300',
                                'group flex rounded-md items-center w-full px-3 py-2 text-sm transition font-semibold',
                            ]"
                            data-test="sign-out-btn"
                            @click="emit('toggleDarkMode')"
                        >
                            <icon-ph-sun v-if="isDark" class="dark:group-hover:text-yellow-200 h-5 w-5 mr-3 text-gray-500 dark:text-gray-300 transition group-hover:text-gray-600" />
                            <icon-ph-moon v-else class="h-5 w-5 mr-3 text-gray-500 dark:text-gray-300 transition group-hover:text-gray-600 dark:group-hover:text-gray-200" />
                            {{ isDark ? 'Light Mode' : "Dark Mode" }}
                        </button>
                    </MenuItem>
                </div>
                <!-- Account actions are hidden in the public demo: it holds no
                     session of its own, so neither does anything useful, and
                     Sign Out is actively harmful. The demo is served same-origin
                     with the real app, so its Sign Out reached the real
                     localStorage — a signed-in FLIP user who opened the demo in
                     the same browser and clicked it got a GlobalSignOut on every
                     device, and the failure path clears the shared origin's
                     storage regardless (FLIP#794 review). src/main.ts leaves
                     Amplify unconfigured in demo builds for the same reason. -->
                <div v-if="!IS_DEMO" class="px-1 py-1">
                    <MenuItem v-slot="{ active }">
                        <button
                            :class="[
                                active ? 'bg-gray-100 dark:bg-dark-surface' : 'text-gray-600 dark:text-gray-300',
                                'group flex rounded-md items-center w-full px-3 py-2 text-sm transition font-semibold',
                            ]"
                            data-test="change-password-btn"
                            @click="changePassword"
                        >
                            <icon-ph-lock
                                class="w-5 h-5 mr-3 text-gray-500 dark:text-gray-300 transition group-hover:text-gray-600 dark:group-hover:text-gray-200"
                                aria-hidden="true"
                            />
                            Change Password
                        </button>
                    </MenuItem>
                </div>
                <div v-if="!IS_DEMO" class="px-1 py-1">
                    <MenuItem v-slot="{ active }">
                        <button
                            :class="[
                                active ? 'bg-gray-100 dark:bg-dark-surface' : 'text-gray-600 dark:text-gray-300',
                                'group flex rounded-md items-center w-full px-3 py-2 text-sm transition font-semibold',
                            ]"
                            data-test="sign-out-btn"
                            @click="signOut"
                        >
                            <icon-mdi-logout
                                class="w-5 h-5 mr-3 text-gray-500 dark:text-gray-300 transition group-hover:text-gray-600 dark:group-hover:text-gray-200"
                                aria-hidden="true"
                            />
                            Sign Out
                        </button>
                    </MenuItem>
                </div>
                <p v-if="appVersion" class="px-4 py-1 text-xs text-right text-gray-500 dark:text-gray-300 truncate">
                    {{ appVersion }}
                </p>
            </MenuItems>
        </transition>
    </Menu>
</template>

<script lang="ts" setup>
import { Menu, MenuButton, MenuItem, MenuItems } from "@headlessui/vue";
import { computed } from "vue";

import { IS_DEMO } from "@/demo/bootstrap";
import { routeChange } from "@/router";

interface IAiUserDropdownProps {
    emailAddress?: string;
    displayName?: string;
    isDark: boolean;
    role?: string;
}

const props = withDefaults(
    defineProps<IAiUserDropdownProps>(), {
        emailAddress: "",
        displayName: "",
        role: ""
    }
);

const emit = defineEmits(["signOut", "toggleDarkMode"]);

const displayName = computed(() => props.displayName || props.emailAddress);

const signOut = () => {
    emit("signOut");
};

const changePassword = () => {
    routeChange.changePassword(props.emailAddress);
};

const appVersion = window.RELEASE_VERSION;

</script>
