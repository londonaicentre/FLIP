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
    <!-- Framed like the Projects/Models views: page gutters around a bordered rounded
         box. The box keeps the old AiCard's load-bearing overflow clipping + white
         canvas without its shadow/ring chrome. -->
    <div data-test="admin-page-gutter" class="w-full h-full p-4 pt-3 md:p-8 md:pt-5">
        <div data-test="users-shell" class="flex w-full h-full overflow-hidden bg-white border border-gray-200 rounded-xl dark:bg-dark-canvas dark:border-dark-border">
            <!-- LEFT RAIL — searchable user list. Below lg only one pane renders at a
                 time (drill-in): the full-width list, or the detail after a row tap.
                 On lg+ it sits beside the editor at 384px, shrinking down to a 240px floor. -->
            <div
                data-test="user-list-rail"
                class="flex-col shrink min-w-[15rem] h-full bg-white lg:border-r border-gray-200 w-full lg:w-96 dark:bg-dark-surface dark:border-dark-border"
                :class="mobileDetailOpen ? 'hidden lg:flex' : 'flex'"
            >
                <div class="flex items-center gap-3 px-4 py-3 border-b border-gray-200 dark:border-dark-border">
                    <h1 class="flex-grow text-lg font-semibold text-gray-900 font-heading dark:text-gray-100">
                        Users
                    </h1>
                    <AiButton
                        light
                        data-test="register-user-btn"
                        aria-label="Register User"
                        tooltip="Register User"
                        @click="showRegisterUserModal = true"
                    >
                        <icon-ph-user-plus class="w-4 h-4 lg:mr-1.5" />
                        <span class="hidden lg:inline">Register User</span>
                    </AiButton>
                </div>
                <div class="px-4 py-3 border-b border-gray-100 dark:border-dark-border">
                    <AiSearch
                        v-model="search"
                        data-test="user-search"
                        placeholder="Search by name, email, org or role"
                    />
                </div>
                <div class="flex-grow min-h-0 overflow-y-auto">
                    <div v-if="!userData?.data" class="p-4 space-y-3" data-test="user-list-loading">
                        <AiSkeleton v-for="n in 6" :key="n" class="w-full h-12" />
                    </div>
                    <ul v-else-if="filteredUsers.length" class="py-1">
                        <li
                            v-for="row in filteredUsers"
                            :key="row.id"
                            data-test="user"
                            class="flex items-center gap-3 px-4 py-2.5 min-h-[60px] cursor-pointer transition border-l-[3px]"
                            :class="row.id === selectedUser?.id
                                ? 'border-primary-500 bg-primary-100 dark:bg-primary-900/40'
                                : 'border-transparent hover:bg-gray-50 dark:hover:bg-dark-canvas'"
                            @click="openUser(row)"
                        >
                            <UserAvatar
                                :name="row.name"
                                :email="row.email"
                                :role-name="row.roles?.[0]?.rolename"
                                :size="36"
                            />
                            <div class="min-w-0 grow">
                                <div class="flex items-center gap-2">
                                    <span class="text-sm font-semibold text-gray-900 truncate dark:text-gray-100">
                                        {{ row.name || row.email }}
                                    </span>
                                    <span
                                        v-if="row.isDisabled"
                                        class="flex-shrink-0 rounded-full bg-red-100 px-1.5 py-px text-[10px] font-bold uppercase tracking-wide text-red-800 dark:bg-red-900/50 dark:text-red-200"
                                    >
                                        Disabled
                                    </span>
                                </div>
                                <div class="text-xs text-gray-500 truncate dark:text-gray-300">
                                    {{ row.organisation || "—" }}
                                </div>
                            </div>
                            <RoleBadge
                                v-if="row.roles?.[0]?.rolename"
                                :role-name="row.roles[0].rolename"
                                dense
                                class="flex-shrink-0"
                            />
                            <icon-heroicons-outline-chevron-right
                                class="w-4 h-4 text-gray-400 dark:text-gray-300 shrink-0 lg:hidden"
                                aria-hidden="true"
                            />
                        </li>
                    </ul>
                    <div v-else class="p-8 text-sm text-center text-gray-500 dark:text-gray-300">
                        {{ search ? "No users match your search." : "There are no users to show" }}
                    </div>
                </div>
                <div class="border-t border-gray-200 dark:border-dark-border">
                    <p
                        v-if="userData?.data"
                        class="px-4 pt-2 text-xs text-gray-500 dark:text-gray-300"
                        data-test="user-count"
                    >
                        {{ countSummary }}
                    </p>
                    <AiPagination
                        :total-pages="userData?.totalPages ?? 1"
                        :current-page="userData?.page ?? 1"
                        slim
                        @page-update="updateUserList"
                    />
                </div>
            </div>

            <!-- RIGHT PANE — selected user editor -->
            <div
                data-test="user-detail-pane"
                class="flex-col flex-grow min-w-0 overflow-hidden"
                :class="mobileDetailOpen ? 'flex' : 'hidden lg:flex'"
            >
                <template v-if="selectedUser">
                    <div
                        data-test="detail-back-header"
                        class="flex items-center px-2 border-b border-gray-200 dark:border-dark-border lg:hidden"
                    >
                        <button
                            type="button"
                            data-test="back-to-users-btn"
                            aria-label="Back to user list"
                            class="flex items-center min-h-[44px] px-2 text-base font-semibold text-primary-500 dark:text-primary-200 transition hover:text-primary-800 dark:hover:text-primary-100"
                            @click="mobileDetailOpen = false"
                        >
                            <icon-mdi-chevron-left class="w-6 h-6" />
                            Users
                        </button>
                    </div>
                    <div class="flex items-center gap-4 px-4 py-4 border-b border-gray-200 md:px-6 dark:border-dark-border">
                        <UserAvatar
                            :name="selectedUser.name"
                            :email="selectedUser.email"
                            :role-name="selectedUser.roles?.[0]?.rolename"
                            :size="48"
                        />
                        <div class="min-w-0 grow">
                            <h2 class="text-xl font-semibold text-gray-900 truncate font-heading dark:text-gray-100">
                                {{ selectedUser.name }}
                            </h2>
                            <p class="text-sm text-gray-500 truncate dark:text-gray-300">
                                {{ selectedUser.organisation }} · {{ selectedUser.email }}
                            </p>
                            <!-- Mobile: the pill drops under the name so nothing shares a
                                 non-wrapping row with the truncating title. -->
                            <span
                                class="mt-1 inline-flex lg:hidden items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold"
                                :class="statusPillClasses"
                                data-test="user-status-mobile"
                            >
                                <span
                                    class="w-1.5 h-1.5 rounded-full"
                                    :class="selectedUser.isDisabled ? 'bg-red-600' : 'bg-green-600'"
                                />
                                {{ selectedUser.isDisabled ? "Disabled" : "Active" }}
                            </span>
                        </div>
                        <span
                            class="hidden lg:inline-flex flex-shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold"
                            :class="statusPillClasses"
                            data-test="user-status"
                        >
                            <span
                                class="w-1.5 h-1.5 rounded-full"
                                :class="selectedUser.isDisabled ? 'bg-red-600' : 'bg-green-600'"
                            />
                            {{ selectedUser.isDisabled ? "Disabled" : "Active" }}
                        </span>
                        <AiButton
                            data-test="save-user-btn"
                            class="hidden lg:block"
                            primary
                            aria-label="Save User"
                            tooltip="Save User"
                            :disabled="!selectedUser.dirty"
                            @click="saveUser"
                        >
                            <icon-mdi-content-save-outline class="w-4 h-4 mr-2" />
                            Save User
                        </AiButton>
                    </div>
                    <div
                        class="flex flex-col overflow-y-auto grow"
                        :class="selectedUser.dirty ? 'pb-28 lg:pb-0' : ''"
                    >
                        <div class="grid gap-4 p-6 border-b border-gray-100 md:grid-cols-3 dark:border-dark-border">
                            <div>
                                <label
                                    for="selected-user-name"
                                    class="block text-xs font-bold tracking-wide text-gray-700 uppercase dark:text-gray-300"
                                >
                                    Name
                                </label>
                                <input
                                    id="selected-user-name"
                                    v-model="selectedUser.name"
                                    data-test="selected-user-name-field"
                                    class="block w-full mt-1 text-sm text-gray-700 transition duration-300 border-gray-300 rounded-md shadow-sm dark:text-gray-300 dark:bg-dark-raised dark:border-dark-border focus:ring-1 focus:border-primary-500 focus:ring-primary-500 dark:focus:ring-primary-400 dark:focus:border-primary-400"
                                    @input="markProfileDirty"
                                >
                            </div>
                            <div>
                                <label
                                    for="selected-user-organisation"
                                    class="block text-xs font-bold tracking-wide text-gray-700 uppercase dark:text-gray-300"
                                >
                                    Organisation
                                </label>
                                <input
                                    id="selected-user-organisation"
                                    v-model="selectedUser.organisation"
                                    data-test="selected-user-organisation-field"
                                    class="block w-full mt-1 text-sm text-gray-700 transition duration-300 border-gray-300 rounded-md shadow-sm dark:text-gray-300 dark:bg-dark-raised dark:border-dark-border focus:ring-1 focus:border-primary-500 focus:ring-primary-500 dark:focus:ring-primary-400 dark:focus:border-primary-400"
                                    @input="markProfileDirty"
                                >
                            </div>
                            <div>
                                <div class="text-xs font-bold tracking-wide text-gray-700 uppercase dark:text-gray-300">
                                    Email
                                </div>
                                <div
                                    data-test="selected-user-email"
                                    class="mt-2 text-sm text-gray-600 break-words dark:text-gray-300"
                                >
                                    {{ selectedUser.email }}
                                </div>
                            </div>
                        </div>
                        <div class="p-6 space-y-3 border-b border-gray-100 dark:border-dark-border">
                            <div>
                                <h3 class="text-base font-bold text-gray-900 font-heading dark:text-gray-100">
                                    Role
                                </h3>
                                <p class="text-sm text-gray-500 dark:text-gray-300">
                                    One role per user. Determines what they can see and do.
                                </p>
                            </div>
                            <div class="space-y-2">
                                <label
                                    v-for="role in allRoles?.roles ?? []"
                                    :key="role.id"
                                    class="flex items-start gap-3 p-3 transition border rounded-md cursor-pointer"
                                    :class="roleCardClasses(role.id)"
                                    :data-test="`select-${(role.rolename || '').toLowerCase()}-role`"
                                >
                                    <input
                                        type="radio"
                                        name="selected-user-role"
                                        class="mt-0.5 text-primary-600 border-gray-300 focus:ring-primary-500"
                                        :value="role.id"
                                        :checked="selectedRoleId === role.id"
                                        @change="selectRole(role)"
                                    >
                                    <span class="flex flex-col min-w-0 gap-1">
                                        <span class="flex flex-wrap items-center gap-2">
                                            <RoleBadge :role-name="role.rolename" />
                                            <span
                                                v-if="role.rolename === 'Admin'"
                                                class="text-xs text-gray-500 dark:text-gray-300"
                                            >
                                                Includes platform admin access
                                            </span>
                                        </span>
                                        <span class="text-sm leading-relaxed text-gray-600 dark:text-gray-300">
                                            {{ role.roledescription }}
                                        </span>
                                    </span>
                                </label>
                            </div>
                            <div
                                v-if="!(allRoles?.roles?.length)"
                                class="text-sm text-center text-gray-500 dark:text-gray-300"
                            >
                                There are no roles available
                            </div>
                        </div>
                        <div class="p-6">
                            <div class="p-4 border border-red-200 rounded-lg bg-red-50 dark:bg-red-950/30 dark:border-red-800">
                                <div class="flex items-start gap-2.5 text-red-700 dark:text-red-300">
                                    <icon-mdi-alert-outline class="flex-shrink-0 w-5 h-5 mt-px" />
                                    <div>
                                        <h3 class="text-sm font-bold font-heading">
                                            Danger Zone
                                        </h3>
                                        <p class="text-xs text-gray-600 dark:text-gray-300">
                                            Account-level operations. Each action is irreversible without user action.
                                        </p>
                                    </div>
                                </div>
                                <div class="grid gap-2 mt-3 sm:grid-cols-2 lg:grid-cols-3">
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
                                        data-test="disable-user-btn"
                                        error
                                        block
                                        @click="dialogDisable = true;"
                                    >
                                        Disable User
                                    </AiButton>
                                    <AiButton
                                        v-if="selectedUser.isDisabled"
                                        data-test="enable-user-btn"
                                        error
                                        block
                                        @click="dialogEnable = true;"
                                    >
                                        Enable User
                                    </AiButton>
                                </div>
                            </div>
                        </div>
                    </div>
                    <!-- Sticky mobile Save bar — slides in whenever the record is dirty,
                         keeping Save thumb-reachable while the detail page-scrolls. -->
                    <div
                        data-test="mobile-save-bar"
                        class="fixed inset-x-0 bottom-0 z-10 px-4 py-3 bg-white/95 dark:bg-dark-surface/95 border-t border-gray-200 dark:border-dark-border shadow-[0_-4px_12px_rgba(0,0,0,0.04)] transition-transform duration-[250ms] ease-out lg:hidden"
                        :class="selectedUser.dirty ? 'translate-y-0' : 'translate-y-[130%]'"
                    >
                        <AiButton
                            primary
                            large
                            block
                            data-test="mobile-save-user-btn"
                            aria-label="Save User"
                            @click="saveUser"
                        >
                            <icon-mdi-content-save-outline class="w-5 h-5 mr-2" />
                            Save User
                        </AiButton>
                    </div>
                </template>
                <div v-else class="flex items-center justify-center h-full p-8">
                    <p class="text-sm text-gray-500 dark:text-gray-300">
                        Select a user or register a new user to begin.
                    </p>
                </div>
            </div>
        </div>
    </div>
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
import AiConfirmModal from "@/components/AiModal/AiConfirmModal.vue";
import AiPagination from "@/components/AiPagination/AiPagination.vue";
import AiSearch from "@/components/AiSearch/AiSearch.vue";
import AiSkeleton from "@/components/AiSkeleton/AiSkeleton.vue";
import RegisterUserModal from "@/partials/users/RegisterUserModal.vue";
import RoleBadge from "@/partials/users/RoleBadge.vue";
import UserAvatar from "@/partials/users/UserAvatar.vue";
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
const pageNumber = ref(1);
const search = ref("");
const selectedUser = ref<IManagedUser>();
// Which pane shows below lg (drill-in). Back keeps selectedUser so a resize
// to desktop still shows the editor; at lg+ both panes render regardless.
const mobileDetailOpen = ref(false);
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
        `/users?pageNumber=${pageNumber.value}&pageSize=${pageSize}`,
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

// Search filters the loaded page client-side — the hub's /users endpoint has no
// search parameter. The handoff calls for server-side search only past ~200 users.
const filteredUsers = computed<IUser[]>(() => {
    const users = userData.value?.data ?? [];
    const query = search.value.trim().toLowerCase();
    if (!query) {
        return users;
    }

    return users.filter((user) =>
        [user.name, user.email, user.organisation, user.roles?.[0]?.rolename].some(
            (field) => (field ?? "").toLowerCase().includes(query)
        )
    );
});

const countSummary = computed(() => {
    const page = userData.value;
    if (!page?.data) {
        return "";
    }
    if (search.value.trim()) {
        return `${filteredUsers.value.length} of ${page.data.length} on this page match`;
    }
    const total = page.totalRecords ?? page.data.length;
    if (!page.data.length) {
        return `0 of ${total}`;
    }
    const start = ((page.page ?? 1) - 1) * (page.pageSize || pageSize) + 1;
    const end = start + page.data.length - 1;

    return `${start}–${end} of ${total}`;
});

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

const openUser = (user: IUser) => {
    setSelectedUser(user);
    mobileDetailOpen.value = true;
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

// Tailwind classes shared by the mobile and desktop status pills.
const statusPillClasses = computed(() =>
    selectedUser.value?.isDisabled
        ? "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200"
        : "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200");

// Tailwind classes for a role card; the selected card gets the primary accent.
const roleCardClasses = (roleId: string) =>
    selectedRoleId.value === roleId
        ? "border-primary-500 bg-primary-100 dark:border-primary-400 dark:bg-primary-900/30"
        : "border-gray-200 hover:border-gray-300 hover:bg-gray-50 dark:border-dark-border dark:hover:bg-dark-canvas";

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
        // Revalidate the SWRV list so the left rail (and the re-selected
        // user) reflect the edit without a manual page reload.
        await refreshUsers();
        Snackbar.success({
            text: "The user has been updated.",
            title: "User updated"
        });
    } catch {
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
    } catch {
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
    } catch {
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
