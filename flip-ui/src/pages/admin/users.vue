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

﻿<!-- eslint-disable vue/multi-word-component-names -->
<route lang="yaml">
    name: User Management
</route>

<template>
    <AiCard class="w-full h-full">
        <div class="flex w-full h-full">
            <div class="flex flex-col h-full w-96 shrink-0">
                <div class="flex items-center p-4 border-b border-r border-gray-300 dark:border-gray-700">
                    <h1 class="flex-grow text-2xl font-semibold font-heading">
                        <span>Users</span>
                    </h1>
                    <AiButton light data-test="register-user-btn" @click="showRegisterUserModal = true">
                        Register User
                    </AiButton>
                </div>
                <div class="w-full overflow-y-auto bg-white border-r border-gray-300 dark:bg-gray-800 dark:border-gray-700 grow">
                    <div v-if="!userData?.data" class="w-full p-4 space-y-4 transition">
                        <AiSkeleton class="w-full h-8" />
                        <AiSkeleton class="w-full h-8" />
                        <AiSkeleton class="w-full h-8" />
                        <AiSkeleton class="w-full h-8" />
                    </div>
                    <VTable v-else :data="userData?.data" class="rounded-none table-fixed border-x-0 ring-0">
                        <template #body="{ rows }">
                            <tr
                                v-for="row in rows"
                                :key="row.id"
                                data-test="user"
                                class="transition hover:cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-900 dark:bg-gray-800"
                                @click="setSelectedUser(row)"
                            >
                                <td class="border-gray-100 dark:border-gray-700 border-x-0">
                                    <div class="flex flex-row items-center">
                                        <div class="truncate">
                                            {{ row.email }}
                                        </div>
                                        <div class="flex items-center ml-auto">
                                            <AiLabel v-if="row.isDisabled" error text="Disabled" class="ml-4" />
                                        </div>
                                    </div>
                                </td>
                            </tr>
                            <tr v-if="!rows.length">
                                <td colspan="3" class="text-center border-gray-100 dark:border-gray-700 border-x-0">
                                    There are no users to show
                                </td>
                            </tr>
                            <tr v-else />
                        </template>
                    </VTable>
                </div>
                <AiPagination
                    :total-pages="userData?.totalPages ?? 1"
                    :current-page="userData?.page ?? 1"
                    slim
                    class="border-r border-gray-100 dark:border-gray-700"
                    @page-update="updateUserList"
                />
            </div>
            <div class="flex flex-col w-full overflow-hidden grow">
                <template v-if="selectedUser">
                    <div class="flex items-center p-4 border-b border-gray-300 dark:border-gray-700">
                        <h1 class="text-2xl font-semibold truncate font-heading grow">
                            <span>{{ selectedUser.email }}</span>
                        </h1>
                        <AiLabel v-if="selectedUser.isDisabled" error text="Disabled" class="mx-2" />
                        <AiButton
                            data-test="save-user-btn"
                            primary
                            class="ml-auto"
                            :disabled="!selectedUser.dirty"
                            @click="saveUser"
                        >
                            Save User
                        </AiButton>
                        <PopoverGroup class="flex items-baseline ml-4 space-x-8">
                            <Popover as="div" class="relative z-10 inline-block text-left">
                                <PopoverButton>
                                    <AiButton data-test="more-options-btn" light>
                                        <icon-mdi-dots-horizontal />
                                    </AiButton>
                                </PopoverButton>
                                <transition
                                    enter-active-class="transition duration-100 ease-out"
                                    enter-from-class="transform scale-95 opacity-0"
                                    enter-to-class="transform scale-100 opacity-100"
                                    leave-active-class="transition duration-75 ease-in"
                                    leave-from-class="transform scale-100 opacity-100"
                                    leave-to-class="transform scale-95 opacity-0"
                                >
                                    <PopoverPanel
                                        class="absolute right-0 p-4 mt-2 origin-top-right bg-white rounded-md shadow-2xl dark:bg-gray-900 dark:ring-white/20 w-60 ring-1 ring-black ring-opacity-5 focus:outline-none"
                                    >
                                        <AiButton
                                            data-test="reset-password-btn"
                                            text-secondary
                                            block
                                            @click="dialogResetPassword = true;"
                                        >
                                            Reset Password
                                        </AiButton>
                                        <AiButton
                                            v-if="!selectedUser.isDisabled"
                                            block
                                            data-test="disable-user-btn"
                                            text-secondary
                                            @click="dialogDisable = true;"
                                        >
                                            Disable User
                                        </AiButton>
                                        <AiButton
                                            v-if="selectedUser.isDisabled"
                                            block
                                            data-test="enable-user-btn"
                                            text
                                            @click="dialogEnable = true;"
                                        >
                                            Enable User
                                        </AiButton>
                                    </PopoverPanel>
                                </transition>
                            </Popover>
                        </PopoverGroup>
                    </div>
                    <div class="flex overflow-hidden grow">
                        <div class="flex-1 overflow-y-auto">
                            <VTable
                                :data="allRoles?.roles.filter((availableRole) => selectedUser?.roles?.every((selectedRole) => selectedRole.rolename !== availableRole.rolename))"
                                class="rounded-none ring-0"
                            >
                                <template #head>
                                    <tr class="text-left">
                                        <th>Available roles</th>
                                        <th />
                                    </tr>
                                </template>
                                <template #body="{ rows }">
                                    <tr v-for="row in rows" :key="row.id">
                                        <td class="border-gray-100 border-x-0">
                                            <span class="w-auto break-words line-clamp-3">
                                                {{ row.rolename }}
                                            </span>
                                            <span class="w-auto font-medium break-words line-clamp-3">
                                                {{ row.roledescription }}
                                            </span>
                                        </td>
                                        <td class="border-gray-100 dark:border-gray-700 border-x-0">
                                            <AiButton
                                                text
                                                :data-test="`add-${(row.rolename || '').toLowerCase()}-btn`"
                                                class="float-right"
                                                @click="add(row)"
                                            >
                                                <icon-ic-baseline-plus />
                                            </AiButton>
                                        </td>
                                    </tr>
                                    <tr v-if="!rows.length">
                                        <td colspan="2" class="text-center border-gray-100 dark:border-gray-700 border-x-0">
                                            There are no available roles
                                        </td>
                                    </tr>
                                    <tr v-else />
                                </template>
                            </VTable>
                        </div>
                        <div class="flex-1 overflow-y-auto">
                            <VTable :data="selectedUser?.roles" class="border-l border-gray-100 rounded-none dark:border-gray-700 ring-0">
                                <template #head>
                                    <tr class="text-left">
                                        <th>Selected Roles</th>
                                        <th />
                                    </tr>
                                </template>
                                <template #body="{ rows }">
                                    <tr v-for="row in rows" :key="row.id">
                                        <td class="border-gray-100 dark:border-gray-700 border-x-0">
                                            <span class="w-auto break-words line-clamp-3">
                                                {{ row.rolename }}
                                            </span>
                                            <span class="w-auto font-medium break-words line-clamp-3">
                                                {{ row.roledescription }}
                                            </span>
                                        </td>
                                        <td class="border-gray-100 dark:border-gray-700 border-x-0">
                                            <AiButton
                                                text
                                                :data-test="`remove-${(row.rolename || '').toLowerCase()}-btn`"
                                                class="float-right"
                                                @click="remove(row)"
                                            >
                                                <icon-ic-baseline-minus />
                                            </AiButton>
                                        </td>
                                    </tr>
                                    <tr v-if="!rows.length">
                                        <td colspan="2" class="text-center border-gray-100 dark:border-gray-700 border-x-0">
                                            There are no selected roles
                                        </td>
                                    </tr>
                                    <tr v-else />
                                </template>
                            </VTable>
                        </div>
                    </div>
                </template>
                <div v-else class="flex items-center h-full">
                    <p class="mx-auto">
                        Select a user or register a new user to begin.
                    </p>
                </div>
            </div>
        </div>
    </AiCard>
    <RegisterUserModal
        title="Register User"
        :dialog="showRegisterUserModal"
        :roles="allRoles?.roles ?? []"
        @close-modal="showRegisterUserModal = false"
        @on-success="refreshUsers()"
    />
    <AiConfirmModal
        :dialog="dialogDisable"
        confirmation-text="Are you sure you want to disable this user?"
        close-button-text="Cancel"
        continue-button-text="Disable User"
        :continue-action="disableUser"
        @close-modal="dialogDisable = false;"
    />
    <AiConfirmModal
        :dialog="dialogEnable"
        confirmation-text="Are you sure you want to enable this user?"
        close-button-text="Cancel"
        continue-button-text="Enable User"
        :continue-action="enableUser"
        @close-modal="dialogEnable = false;"
    />
    <AiConfirmModal
        :dialog="dialogResetPassword"
        confirmation-text="Are you sure you want to reset this user's password?"
        close-button-text="Cancel"
        continue-button-text="Reset Password"
        :continue-action="resetPassword"
        @close-modal="dialogResetPassword = false;"
    />
</template>

<script setup lang="ts">
import {
    Popover,
    PopoverButton,
    PopoverGroup,
    PopoverPanel
} from "@headlessui/vue";
import useSWRV from "swrv";
import { onBeforeMount, ref } from "vue";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import AiLabel from "@/components/AiLabel/AiLabel.vue";
import AiConfirmModal from "@/components/AiModal/AiConfirmModal.vue";
import AiPagination from "@/components/AiPagination/AiPagination.vue";
import RegisterUserModal from "@/partials/users/RegisterUserModal.vue";
import { routeChange } from "@/router";
import { getRoles, IRole } from "@/services/role-service";
import {
    getUsers,
    IUser,
    IUserDisabledStateDto,
    updateUserDisabledState,
    updateUserRoles
} from "@/services/user-service";
import { useAuthStore } from "@/store/auth";
import { useErrorStore } from "@/store/error";
import { canAccessRoute } from "@/utils/route-validator";
import { Snackbar } from "@/utils/snackbar";

interface IManagedUser extends IUser {
    dirty: boolean
}

const authStore = useAuthStore();
const errorStore = useErrorStore();
const pageSize = 20;
const searchQueryParam = ref("");
const pageNumber = ref(1);
const selectedUser = ref<IManagedUser>();
const showRegisterUserModal = ref(false);
const dialogDisable = ref(false);
const dialogEnable = ref(false);
const dialogResetPassword = ref(false);

onBeforeMount(async () => {
    if(!(await canAccessRoute(authStore, ["CanManageUsers"]))){
        errorStore.setError();

        routeChange.viewProjects();
    }
});

const { data: userData, mutate: userMutate } = useSWRV(
    () =>
        `/users/?pageNumber=${pageNumber.value}&pageSize=${pageSize}${searchQueryParam.value}`,
    getUsers,
    {
        dedupingInterval: 5_000,
        shouldRetryOnError: false
    }
);

const { data: allRoles } = useSWRV(
    () =>
        "/roles/",
    getRoles,
    {
        dedupingInterval: 5_000,
        shouldRetryOnError: false
    }
);

const updateUserList = (newPageNumber: number) => {
    pageNumber.value = newPageNumber;
};

const setSelectedUser = (user: IManagedUser) => {
    selectedUser.value = {
        ...user,
        roles: user.roles ?? [], // Ensure it's not undefined
        dirty: false
    };
};

const add = (role: IRole) => {
    if (selectedUser.value) {
        selectedUser.value.dirty = true;
        selectedUser.value.roles.push(role);
    }
};

const remove = (role: IRole) => {
    if (selectedUser.value) {
        if (selectedUser.value.roles.length > 1) {
            selectedUser.value.dirty = true;
            const index = selectedUser.value.roles.indexOf(role);
            selectedUser.value.roles.splice(index, 1);
        } else {
            Snackbar.error({
                text: "You need to have at least 1 role assigned to a user.",
                title: "Error"
            });
        }
    }
};

const saveUser = () => {
    if (selectedUser.value) {
        try {
            updateUserRoles(selectedUser.value?.id, selectedUser.value?.roles.map((role) => role.id));
            selectedUser.value.dirty = false;
        } catch (e) {
            Snackbar.error({
                text: "The user could not be updated, please try again.",
                title: "Update failed"
            });
        }
    }
};

const disableUser = async () => {
    dialogDisable.value = false;
    await updateUserState(true);
    Snackbar.success({
        text: "The user has been disabled.",
        title: "User disabled"
    });
};

const enableUser = async () => {
    dialogEnable.value = false;
    await updateUserState(false);
    Snackbar.success({
        text: "The user has been enabled.",
        title: "User enabled"
    });
};

const updateUserState = async (disabled: boolean) => {
    try {
        const state: IUserDisabledStateDto = { disabled: disabled };
        await updateUserDisabledState(selectedUser.value?.id as string, state);
        await refreshUsers();
    } catch (e) {
        Snackbar.error({
            text: "There was an error, please try again.",
            title: "User not updated"
        });
        errorStore.setError();
    }
};

const resetPassword = () => {
    if (selectedUser.value) {
        dialogResetPassword.value = false;

        authStore.resetPassword(selectedUser.value?.email);

        Snackbar.success({
            text: "The user's password has been reset.",
            title: "Password reset"
        });
    }
};

const refreshUsers = async () => {
    const previous = selectedUser.value;

    await userMutate();

    if (!previous) return;

    const newSelectedUser =
        userData.value?.data.find((user) => user.id === previous.id);
    if (newSelectedUser) {
        setSelectedUser(newSelectedUser);
    }
};
</script>
