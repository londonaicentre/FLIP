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
                                            {{ formatUserListName(row) }}
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
                        <div class="min-w-0 grow">
                            <h1 class="text-2xl font-semibold truncate font-heading">
                                <span>{{ selectedUser.name }}</span>
                            </h1>
                            <p class="text-sm text-gray-500 truncate dark:text-gray-400">
                                {{ selectedUser.organisation }} · {{ selectedUser.email }}
                            </p>
                        </div>
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
                    </div>
                    <div class="flex flex-col overflow-y-auto grow">
                        <div class="grid gap-4 p-4 border-b border-gray-100 md:grid-cols-3 dark:border-gray-700">
                            <div>
                                <label for="selected-user-name" class="block text-sm font-bold text-gray-700 dark:text-gray-400">
                                    Name
                                </label>
                                <input
                                    id="selected-user-name"
                                    v-model="selectedUser.name"
                                    data-test="selected-user-name-field"
                                    class="block w-full mt-1 text-sm text-gray-700 transition duration-300 border-gray-300 rounded-md shadow-sm dark:text-gray-300 dark:bg-gray-700 dark:border-gray-700 focus:ring-1 focus:border-primary-500 focus:ring-primary-500 dark:focus:ring-primary-400 dark:focus:border-primary-400"
                                    @input="markProfileDirty"
                                >
                            </div>
                            <div>
                                <label for="selected-user-organisation" class="block text-sm font-bold text-gray-700 dark:text-gray-400">
                                    Organisation
                                </label>
                                <input
                                    id="selected-user-organisation"
                                    v-model="selectedUser.organisation"
                                    data-test="selected-user-organisation-field"
                                    class="block w-full mt-1 text-sm text-gray-700 transition duration-300 border-gray-300 rounded-md shadow-sm dark:text-gray-300 dark:bg-gray-700 dark:border-gray-700 focus:ring-1 focus:border-primary-500 focus:ring-primary-500 dark:focus:ring-primary-400 dark:focus:border-primary-400"
                                    @input="markProfileDirty"
                                >
                            </div>
                            <div>
                                <div class="text-sm font-bold text-gray-700 dark:text-gray-400">
                                    Email
                                </div>
                                <div data-test="selected-user-email" class="mt-2 text-sm text-gray-600 break-words dark:text-gray-300">
                                    {{ selectedUser.email }}
                                </div>
                            </div>
                        </div>
                        <div class="border-b border-gray-100 dark:border-gray-700">
                            <div class="p-4 space-y-3">
                                <h2 class="text-sm font-bold text-gray-700 dark:text-gray-400">
                                    Role
                                </h2>
                                <div class="space-y-2">
                                    <label
                                        v-for="role in allRoles?.roles ?? []"
                                        :key="role.id"
                                        class="flex items-start gap-3 p-3 border border-gray-200 rounded-md cursor-pointer dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-900"
                                        :data-test="`select-${(role.rolename || '').toLowerCase()}-role`"
                                    >
                                        <input
                                            type="radio"
                                            name="selected-user-role"
                                            class="mt-1 text-primary-600 border-gray-300 focus:ring-primary-500"
                                            :value="role.id"
                                            :checked="selectedRoleId === role.id"
                                            @change="selectRole(role)"
                                        >
                                        <span class="min-w-0">
                                            <span class="block text-sm font-semibold text-gray-900 dark:text-gray-100">
                                                {{ role.rolename }}
                                            </span>
                                            <span class="block text-sm text-gray-500 dark:text-gray-400">
                                                {{ role.roledescription }}
                                            </span>
                                        </span>
                                    </label>
                                </div>
                                <div v-if="!(allRoles?.roles?.length)" class="text-sm text-center text-gray-500 dark:text-gray-400">
                                    There are no roles available
                                </div>
                            </div>
                        </div>
                        <div class="p-4">
                            <div class="p-4 border border-red-300 rounded-md bg-red-50 dark:bg-red-950/30 dark:border-red-800">
                                <h2 class="text-sm font-bold text-red-700 dark:text-red-300">
                                    Danger Zone
                                </h2>
                                <div class="grid gap-2 mt-3 sm:grid-cols-2 lg:grid-cols-4">
                                    <AiButton
                                        data-test="reset-password-btn"
                                        error
                                        block
                                        @click="dialogResetPassword = true;"
                                    >
                                        Reset Password
                                    </AiButton>
                                    <AiButton
                                        data-test="reset-mfa-btn"
                                        error
                                        block
                                        @click="dialogResetMfa = true;"
                                    >
                                        Reset MFA
                                    </AiButton>
                                    <AiButton
                                        v-if="!selectedUser.isDisabled"
                                        block
                                        data-test="disable-user-btn"
                                        error
                                        @click="dialogDisable = true;"
                                    >
                                        Disable User
                                    </AiButton>
                                    <AiButton
                                        v-if="selectedUser.isDisabled"
                                        block
                                        data-test="enable-user-btn"
                                        error
                                        @click="dialogEnable = true;"
                                    >
                                        Enable User
                                    </AiButton>
                                </div>
                            </div>
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
    <AiConfirmModal
        :dialog="dialogResetMfa"
        confirmation-text="Reset this user's MFA device? They will need to enrol a new authenticator on their next sign-in."
        close-button-text="Cancel"
        continue-button-text="Reset MFA"
        :continue-action="resetMfa"
        @close-modal="dialogResetMfa = false;"
    />
</template>

<script setup lang="ts">
import useSWRV from "swrv";
import { computed, onBeforeMount, ref } from "vue";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import AiLabel from "@/components/AiLabel/AiLabel.vue";
import AiConfirmModal from "@/components/AiModal/AiConfirmModal.vue";
import AiPagination from "@/components/AiPagination/AiPagination.vue";
import RegisterUserModal from "@/partials/users/RegisterUserModal.vue";
import { routeChange } from "@/router";
import { getRoles, IRole } from "@/services/role-service";
import { getUsers,
    IUser,
    IUserDisabledStateDto,
    resetUserMfa,
    updateUserDisabledState,
    updateUserProfile,
    updateUserRoles } from "@/services/user-service";
import { useAuthStore } from "@/store/auth";
import { useErrorStore } from "@/store/error";
import { canAccessRoute } from "@/utils/route-validator";
import { Snackbar } from "@/utils/snackbar";

interface IManagedUser extends IUser {
    dirty: boolean,
    profileDirty: boolean,
    rolesDirty: boolean
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
const dialogResetMfa = ref(false);

onBeforeMount(async () => {
    if(!(await canAccessRoute(authStore, ["CanManageUsers"]))){
        errorStore.setError();

        routeChange.viewProjects();
    }
});

const { data: userData, mutate: userMutate } = useSWRV(
    () =>
        `/users?pageNumber=${pageNumber.value}&pageSize=${pageSize}${searchQueryParam.value}`,
    getUsers,
    {
        dedupingInterval: 5_000,
        shouldRetryOnError: false
    }
);

const { data: allRoles } = useSWRV(
    () =>
        "/roles",
    getRoles,
    {
        dedupingInterval: 5_000,
        shouldRetryOnError: false
    }
);

const updateUserList = (newPageNumber: number) => {
    pageNumber.value = newPageNumber;
};

const setSelectedUser = (user: IUser) => {
    selectedUser.value = {
        ...user,
        roles: user.roles ?? [], // Ensure it's not undefined
        dirty: false,
        profileDirty: false,
        rolesDirty: false
    };
};

const formatUserListName = (user: IUser) => {
    const name = user.name || user.email;
    return user.organisation ? `${name} (${user.organisation})` : name;
};

const markProfileDirty = () => {
    if (selectedUser.value) {
        selectedUser.value.dirty = true;
        selectedUser.value.profileDirty = true;
    }
};

const selectedRoleId = computed(() => selectedUser.value?.roles?.[0]?.id);

const selectRole = (role: IRole) => {
    if (!selectedUser.value || selectedRoleId.value === role.id) return;

    selectedUser.value.roles = [role];
    selectedUser.value.dirty = true;
    selectedUser.value.rolesDirty = true;
};

const saveUser = async () => {
    if (!selectedUser.value) return;
    try {
        if (selectedUser.value.profileDirty) {
            await updateUserProfile(
                selectedUser.value.id,
                {
                    name: selectedUser.value.name,
                    organisation: selectedUser.value.organisation
                }
            );
        }
        if (selectedUser.value.rolesDirty) {
            await updateUserRoles(
                selectedUser.value.id,
                selectedUser.value.roles[0] ? [selectedUser.value.roles[0].id] : []
            );
        }
        selectedUser.value.dirty = false;
        selectedUser.value.profileDirty = false;
        selectedUser.value.rolesDirty = false;
        Snackbar.success({
            text: "The user has been updated.",
            title: "User updated"
        });
    } catch (e) {
        Snackbar.error({
            text: "The user could not be updated, please try again.",
            title: "Update failed"
        });
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

const resetMfa = async () => {
    if (!selectedUser.value) return;
    dialogResetMfa.value = false;

    try {
        await resetUserMfa(selectedUser.value.id);
        Snackbar.success({
            text: "The user's authenticator has been cleared. They will enrol a new one on next sign-in.",
            title: "MFA reset"
        });
    } catch (e) {
        Snackbar.error({
            text: "There was an error resetting MFA, please try again.",
            title: "MFA reset failed"
        });
        errorStore.setError();
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
