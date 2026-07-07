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

<route lang="yaml">
name: Access Requests
</route>

<template>
    <!-- Owns its gutters: admin.vue's content wrapper no longer pads subpages. -->
    <div data-test="admin-page-gutter" class="w-full h-full p-4 md:p-8">
        <AiCard class="w-full h-full">
            <div class="flex flex-col w-full h-full">
                <div class="flex items-center gap-3 px-6 py-4 border-b border-gray-200 dark:border-gray-700">
                    <h1 class="flex-grow text-lg font-semibold text-gray-900 font-heading dark:text-gray-100">
                        Access Requests
                    </h1>
                    <label for="status-filter" class="sr-only">Filter by status</label>
                    <select
                        id="status-filter"
                        v-model="statusFilter"
                        data-test="status-filter"
                        class="block py-2 pl-3 pr-8 text-sm text-gray-900 bg-white border border-gray-300 rounded-md shadow-sm dark:bg-gray-700 dark:text-gray-100 dark:border-gray-700 focus:border-primary-500 focus:ring-primary-500"
                    >
                        <option value="PENDING">
                            Pending
                        </option>
                        <option value="ENROLLED">
                            Enrolled
                        </option>
                        <option value="DISMISSED">
                            Dismissed
                        </option>
                        <option value="">
                            All
                        </option>
                    </select>
                </div>

                <div class="flex-grow min-h-0 overflow-y-auto">
                    <div v-if="!requestData?.data" class="p-6 space-y-3" data-test="access-requests-loading">
                        <AiSkeleton v-for="n in 6" :key="n" class="w-full h-12" />
                    </div>
                    <div v-else-if="requests.length" class="overflow-x-auto">
                        <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                            <thead class="bg-gray-50 dark:bg-gray-900">
                                <tr>
                                    <th scope="col" class="px-6 py-3 text-xs font-semibold tracking-wide text-left text-gray-500 uppercase dark:text-gray-300">
                                        Name
                                    </th>
                                    <th scope="col" class="px-6 py-3 text-xs font-semibold tracking-wide text-left text-gray-500 uppercase dark:text-gray-300">
                                        Email
                                    </th>
                                    <th scope="col" class="px-6 py-3 text-xs font-semibold tracking-wide text-left text-gray-500 uppercase dark:text-gray-300">
                                        Reason
                                    </th>
                                    <th scope="col" class="px-6 py-3 text-xs font-semibold tracking-wide text-left text-gray-500 uppercase dark:text-gray-300">
                                        Submitted
                                    </th>
                                    <th scope="col" class="px-6 py-3 text-xs font-semibold tracking-wide text-left text-gray-500 uppercase dark:text-gray-300">
                                        Status
                                    </th>
                                    <th scope="col" class="px-6 py-3 text-xs font-semibold tracking-wide text-right text-gray-500 uppercase dark:text-gray-300">
                                        Actions
                                    </th>
                                </tr>
                            </thead>
                            <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
                                <tr v-for="row in requests" :key="row.id" data-test="access-request-row">
                                    <td class="px-6 py-4 text-sm font-medium text-gray-900 whitespace-nowrap dark:text-gray-100">
                                        {{ row.fullName }}
                                    </td>
                                    <td class="px-6 py-4 text-sm text-gray-500 whitespace-nowrap dark:text-gray-300">
                                        {{ row.email }}
                                    </td>
                                    <td class="max-w-xs px-6 py-4 text-sm text-gray-500 truncate dark:text-gray-300" :title="row.reasonForAccess">
                                        {{ row.reasonForAccess }}
                                    </td>
                                    <td class="px-6 py-4 text-sm text-gray-500 whitespace-nowrap dark:text-gray-300">
                                        {{ formatDate(row.createdAt) }}
                                    </td>
                                    <td class="px-6 py-4 whitespace-nowrap">
                                        <span
                                            class="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold"
                                            :class="statusStyle(row.status)"
                                            data-test="access-request-status"
                                        >
                                            {{ statusLabel(row.status) }}
                                        </span>
                                    </td>
                                    <td class="px-6 py-4 text-right whitespace-nowrap">
                                        <div v-if="row.status === 'PENDING'" class="flex justify-end gap-2">
                                            <AiButton primary data-test="enroll-btn" @click="openEnroll(row)">
                                                Enroll
                                            </AiButton>
                                            <AiButton error data-test="dismiss-btn" @click="openDismiss(row)">
                                                Dismiss
                                            </AiButton>
                                        </div>
                                        <span v-else class="text-xs text-gray-400 dark:text-gray-300">—</span>
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <div v-else class="p-8 text-sm text-center text-gray-500 dark:text-gray-300" data-test="access-requests-empty">
                        There are no access requests to show.
                    </div>
                </div>

                <div class="border-t border-gray-200 dark:border-gray-700">
                    <AiPagination
                        :total-pages="requestData?.totalPages ?? 1"
                        :current-page="requestData?.page ?? 1"
                        slim
                        @page-update="updatePage"
                    />
                </div>
            </div>
        </AiCard>
    </div>

    <RegisterUserModal
        title="Enroll User"
        :dialog="showEnrollModal"
        :roles="allRoles?.roles ?? []"
        :initial-name="selectedRequest?.fullName ?? ''"
        :initial-email="selectedRequest?.email ?? ''"
        @close-modal="showEnrollModal = false"
        @on-success="onEnrolled"
    />
    <AiConfirmModal
        :dialog="showDismissModal"
        confirmation-text="Dismiss this access request? The requester will not be enrolled."
        close-button-text="Cancel"
        continue-button-text="Dismiss Request"
        :continue-action="confirmDismiss"
        @close-modal="showDismissModal = false;"
    />
</template>

<script setup lang="ts">
import useSWRV from "swrv";
import { computed, onBeforeMount, ref, watch } from "vue";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import AiConfirmModal from "@/components/AiModal/AiConfirmModal.vue";
import AiPagination from "@/components/AiPagination/AiPagination.vue";
import AiSkeleton from "@/components/AiSkeleton/AiSkeleton.vue";
import RegisterUserModal from "@/partials/users/RegisterUserModal.vue";
import { routeChange } from "@/router";
import { getRoles } from "@/services/role-service";
import { AccessRequestStatus,
    getAccessRequests,
    IAccessRequestRecord,
    updateAccessRequestStatus } from "@/services/user-service";
import { useAuthStore } from "@/store/auth";
import { useErrorStore } from "@/store/error";
import { canAccessRoute } from "@/utils/route-validator";
import { Snackbar } from "@/utils/snackbar";

const authStore = useAuthStore();
const errorStore = useErrorStore();
const pageSize = 20;
const pageNumber = ref(1);
const statusFilter = ref<AccessRequestStatus | "">("PENDING");
const selectedRequest = ref<IAccessRequestRecord>();
const showEnrollModal = ref(false);
const showDismissModal = ref(false);

onBeforeMount(async () => {
    if (!(await canAccessRoute(authStore, ["CanManageUsers"]))) {
        errorStore.setError();

        routeChange.viewProjects();
    }
});

const { data: requestData, mutate: refreshRequests } = useSWRV(
    () =>
        `/users/access?pageNumber=${pageNumber.value}&pageSize=${pageSize}`
        + (statusFilter.value ? `&status=${statusFilter.value}` : ""),
    getAccessRequests,
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

const requests = computed<IAccessRequestRecord[]>(() => requestData.value?.data ?? []);

// Reset to the first page whenever the status filter changes so we never land on
// an out-of-range page for the newly-filtered, smaller result set.
watch(statusFilter, () => {
    pageNumber.value = 1;
});

const updatePage = (page: number) => {
    pageNumber.value = page;
};

// Backend timestamps are naive UTC (datetime.utcnow); tag as UTC so the browser
// doesn't reinterpret them as local time.
const formatDate = (iso: string): string => {
    const asUtc = /[zZ]|[+-]\d{2}:\d{2}$/.test(iso) ? iso : `${iso}Z`;

    return new Date(asUtc).toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric"
    });
};

const STATUS_LABELS: Record<AccessRequestStatus, string> = {
    PENDING: "Pending",
    ENROLLED: "Enrolled",
    DISMISSED: "Dismissed"
};
const statusLabel = (status: AccessRequestStatus): string => STATUS_LABELS[status];

const STATUS_STYLES: Record<AccessRequestStatus, string> = {
    PENDING: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
    ENROLLED: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200",
    DISMISSED: "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300"
};
const statusStyle = (status: AccessRequestStatus): string => STATUS_STYLES[status];

const openEnroll = (row: IAccessRequestRecord) => {
    selectedRequest.value = row;
    showEnrollModal.value = true;
};

// Fired once the register-user flow succeeds — the Cognito user now exists, so
// move the originating request to ENROLLED. Best-effort: the user is created
// regardless, so a status-update failure is surfaced but not fatal.
const onEnrolled = async () => {
    const request = selectedRequest.value;
    if (!request) {
        return;
    }

    try {
        await updateAccessRequestStatus(request.id, "ENROLLED");
        await refreshRequests();
    } catch {
        Snackbar.error({
            title: "Couldn't update request",
            text: "The user was created, but the request could not be marked as enrolled."
        });
        errorStore.setError();
    } finally {
        selectedRequest.value = undefined;
    }
};

const openDismiss = (row: IAccessRequestRecord) => {
    selectedRequest.value = row;
    showDismissModal.value = true;
};

const confirmDismiss = async () => {
    const request = selectedRequest.value;
    if (!request) {
        return;
    }

    try {
        await updateAccessRequestStatus(request.id, "DISMISSED");
        Snackbar.success({
            title: "Request dismissed",
            text: "The access request has been dismissed."
        });
        await refreshRequests();
    } catch {
        Snackbar.error({
            title: "Couldn't dismiss request",
            text: "Please try again."
        });
        errorStore.setError();
    } finally {
        showDismissModal.value = false;
        selectedRequest.value = undefined;
    }
};
</script>
