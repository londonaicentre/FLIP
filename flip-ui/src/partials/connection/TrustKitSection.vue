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

<!-- The trust's kit (keys + FL credentials, one encrypted file) on its detail card (FLIP#1852). Only the trust's own
     admin and hub admins see it; for anyone else it renders nothing and asks the hub nothing.
     `compact` is the Connection Status table's cell: the download button alone, which asks the hub for the kit's
     status only once it is clicked, so a table of trusts costs no request per row. -->
<template>
    <!-- Clicks and keys stop here: the table row behind the button opens the trust's drawer. -->
    <span
        v-if="canManageKit && compact"
        data-test="trust-kit-cell"
        @click.stop
        @keydown.stop
    >
        <AiButton
            primary
            small
            class="whitespace-nowrap"
            data-test="kit-download-btn"
            :aria-label="`Download the ${trustLabel} trust kit`"
            @click="askDownload"
        >
            Download trust kit
        </AiButton>
    </span>
    <div
        v-else-if="canManageKit"
        data-test="trust-kit-section"
        class="px-[22px] pt-4 pb-[18px] border-b border-gray-200 dark:border-dark-border bg-primary-100/60 dark:bg-dark-canvas"
    >
        <div class="flex items-center justify-between gap-2">
            <p class="text-xs text-gray-500 dark:text-gray-300">
                Trust kit
            </p>
            <span
                v-if="status?.update_available"
                data-test="kit-update-chip"
                class="px-1.5 py-0.5 rounded-full text-xs font-bold whitespace-nowrap bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
            >Kit update available</span>
        </div>

        <p v-if="statusError" data-test="kit-error" class="mt-2 text-[12.5px] text-red-700 dark:text-red-300">
            The kit status could not be loaded. Reload the page to try again.
        </p>
        <p v-else-if="!status" data-test="kit-loading" class="mt-2 text-[12.5px] text-gray-600 dark:text-gray-300">
            Loading…
        </p>
        <template v-else>
            <div class="flex items-center justify-between gap-3 mt-2">
                <div class="min-w-0">
                    <p data-test="kit-status" class="text-[13px] font-semibold text-gray-900 dark:text-gray-100">
                        {{ statusText }}
                    </p>
                    <p class="text-[11.5px] text-gray-600 dark:text-gray-300 mt-0.5">
                        Keys and FL credentials for this trust's node.
                    </p>
                </div>
                <AiButton
                    primary
                    small
                    class="shrink-0"
                    data-test="kit-download-btn"
                    @click="askDownload"
                >
                    Download kit
                </AiButton>
            </div>
            <AiButton
                v-if="status.keys_issued"
                text
                text-primary
                small
                class="mt-1 -ml-2.5 w-fit"
                data-test="kit-rotate-btn"
                :disabled="!trust.code"
                :tooltip="trust.code ? '' : 'This trust has no code, so new keys cannot be issued from here.'"
                @click="asking = 'rotate'"
            >
                Issue new keys
            </AiButton>
        </template>
    </div>

    <!-- Shared by both forms. -->
    <template v-if="canManageKit">
        <AiConfirmModal
            :dialog="asking === 'download'"
            :title="`Download the ${trustLabel} trust kit`"
            :confirmation-text="downloadText"
            :continue-action="() => issue(false)"
            :submitting="submitting || !status"
            close-button-text="Cancel"
            continue-button-text="Download"
            @close-modal="asking = null"
        />

        <AiConfirmModal
            :dialog="asking === 'rotate'"
            :title="`Issue new keys for ${trustLabel}`"
            :typing-confirmation="trust.code ?? ''"
            :placeholder="trust.code ?? ''"
            :continue-action="() => issue(true)"
            :submitting="submitting"
            close-button-text="Cancel"
            continue-button-text="Issue new keys"
            @close-modal="asking = null"
        >
            <template #confirmation>
                <strong>The kit installed today will stop working.</strong>
                The trust goes offline until the new kit is installed on the trust host.
                <span class="block mt-2">Type {{ trust.code }} to confirm.</span>
            </template>
        </AiConfirmModal>
    </template>
</template>

<script setup lang="ts">
import { isAxiosError } from "axios";
import useSWRV from "swrv";
import { computed, ref } from "vue";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiConfirmModal from "@/components/AiModal/AiConfirmModal.vue";
import { usePermissions } from "@/composables/usePermissions";
import { downloadTrustKit,
    getTrustKitStatus,
    ITrustKitStatus,
    ITrustResponse,
    rotateTrustKit } from "@/services/trust-service";
import { useAuthStore } from "@/store/auth";
import { extractErrorDetail } from "@/utils/api-errors";
import { issuedKit, saveKitFile } from "@/utils/issued-trust-kit";
import { Snackbar } from "@/utils/snackbar";
import { shortDate } from "@/utils/trust-decisions";

const props = defineProps<{ trust: Pick<ITrustResponse, "id" | "name" | "code">; compact?: boolean }>();

const authStore = useAuthStore();
const { isAdmin } = usePermissions();
const isThisTrustsAdmin = computed(() => authStore.trustAdminOf?.id === props.trust.id);
const canManageKit = computed(() => isAdmin.value || isThisTrustsAdmin.value);

const trustLabel = computed(() => props.trust.code ?? props.trust.name);

// A null key fetches nothing, so a viewer who may not manage the kit never calls the route, and the
// compact form calls it only once its button is clicked.
const wanted = ref(!props.compact);
const { data: status, error: statusError, mutate: refreshStatus } = useSWRV<ITrustKitStatus>(
    () => (canManageKit.value && wanted.value ? `/trust/${props.trust.id}/kit` : null),
    () => getTrustKitStatus(props.trust.id),
    {
        dedupingInterval: 5_000,
        shouldRetryOnError: false
    }
);

const statusText = computed(() => {
    if (!status.value?.keys_issued) return "Not yet downloaded";
    const last = status.value.last_download;
    // Keys without a download: the trust was registered from the command line.
    if (!last) return "Keys issued; not downloaded from here";

    return `Downloaded ${shortDate(last.at)}${last.by ? ` by ${last.by}` : ""}`;
});

const downloadText = computed(() => {
    if (!status.value) return statusError.value ? "The kit status could not be loaded." : "Checking this trust's kit…";
    if (status.value.keys_issued) {
        return "This kit carries the current hub settings and FL credentials. It does not change the trust's keys. "
            + "The download is recorded against your account.";
    }
    const text = "The kit holds this trust's keys. Download it on a machine you trust, copy it to the trust host, "
        + "then delete the download. The download is recorded against your account.";

    return isThisTrustsAdmin.value
        ? text
        : `${text} This is the only download that carries the keys. After it, the site's admin will only get updates.`;
});

const asking = ref<"download" | "rotate" | null>(null);
const askDownload = () => {
    wanted.value = true;
    asking.value = "download";
};
const submitting = ref(false);
const issue = async (newKeys: boolean) => {
    submitting.value = true;
    try {
        const kit = newKeys
            ? await rotateTrustKit(props.trust.id, props.trust.code ?? "")
            : await downloadTrustKit(props.trust.id);
        saveKitFile(kit);
        issuedKit.value = {
            ...kit,
            trustLabel: trustLabel.value
        };
    } catch (e) {
        // No reply is not a refusal: the hub issues the keys before it answers, so they may exist now.
        const maybeIssued = (newKeys || !status.value?.keys_issued) && isAxiosError(e) && !e.response;
        Snackbar.error({
            title: maybeIssued ? "No answer from the hub" : newKeys ? "New keys not issued" : "Kit not downloaded",
            text: maybeIssued
                ? "The hub did not answer, and may have issued this trust's keys all the same. If the kit status now "
                    + "shows them issued, use Issue new keys to get a kit that carries them."
                : extractErrorDetail(e, "The kit could not be built, please try again.")
        });
    } finally {
        void refreshStatus();
        submitting.value = false;
        asking.value = null;
    }
};
</script>
