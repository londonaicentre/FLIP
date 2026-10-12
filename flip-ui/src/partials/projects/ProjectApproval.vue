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
    <AiCard class="flex flex-col h-full">
        <div class="p-4 shrink-0">
            <div class="flex items-baseline justify-between gap-3">
                <h2 class="text-lg font-semibold font-heading grow leading-loose">
                    Trust Approval
                </h2>
                <span
                    v-if="sortedTrusts.length"
                    data-test="trust-approval-count"
                    class="font-mono text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-300 shrink-0"
                >
                    {{ countLabel }}
                </span>
            </div>
        </div>
        <div class="flex flex-col flex-1 min-h-0">
            <!-- The scroller wraps the <ul> rather than being the <ul>, so its border-y
                 stays pinned at the top of the scroll area instead of scrolling away. -->
            <div class="w-full flex-1 min-h-0 overflow-y-auto text-xs">
                <ul role="list" class="border-gray-200 divide-y divide-gray-200 dark:divide-dark-border dark:border-dark-border border-y">
                    <li v-for="(trust, idx) in sortedTrusts" :key="trust.id">
                        <div class="flex items-center gap-3 px-4 py-3 transition hover:bg-gray-50 dark:hover:bg-dark-surface group">
                            <div class="flex-1 min-w-0">
                                <p
                                    class="text-xs font-semibold truncate text-primary-600 dark:text-primary-200"
                                    :title="trust.name"
                                >
                                    {{ trust.code || trust.name }}
                                </p>
                                <p
                                    class="mt-0.5 text-[11px] truncate text-gray-500 dark:text-gray-300"
                                    :data-test="`trust-decision-${idx}`"
                                >
                                    {{ decisionLine(trust) }}
                                </p>
                            </div>
                            <div
                                v-if="rowCanDecide(trust)"
                                class="inline-flex shrink-0 rounded-md"
                                role="group"
                                :aria-label="`Decision for ${trust.name}`"
                            >
                                <button
                                    type="button"
                                    :data-test="`trust-approve-${idx}`"
                                    :aria-pressed="choices[trust.id] === 'APPROVED'"
                                    :aria-label="`Approve ${trust.name}`"
                                    :title="`Approve ${trust.name}`"
                                    :disabled="approving"
                                    :class="[
                                        CHOICE_BASE_CLASS,
                                        'rounded-l-md',
                                        choices[trust.id] === 'APPROVED' ? CHOICE_CLASS.APPROVED : CHOICE_CLASS.idle
                                    ]"
                                    @click="choose(trust, 'APPROVED')"
                                >
                                    <icon-ph-check-bold class="w-3.5 h-3.5" aria-hidden="true" />
                                    Approve
                                </button>
                                <button
                                    type="button"
                                    :data-test="`trust-decline-${idx}`"
                                    :aria-pressed="choices[trust.id] === 'DECLINED'"
                                    :aria-label="`Decline ${trust.name}`"
                                    :title="`Decline ${trust.name}`"
                                    :disabled="approving"
                                    :class="[
                                        CHOICE_BASE_CLASS,
                                        '-ml-px rounded-r-md',
                                        choices[trust.id] === 'DECLINED' ? CHOICE_CLASS.DECLINED : CHOICE_CLASS.idle
                                    ]"
                                    @click="choose(trust, 'DECLINED')"
                                >
                                    <icon-ph-x-bold class="w-3.5 h-3.5" aria-hidden="true" />
                                    Decline
                                </button>
                            </div>
                            <span
                                v-else
                                :class="['inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-semibold shrink-0', chipFor(trust).class]"
                                :data-test="`trust-status-chip-${idx}`"
                                :data-status="trust.status"
                                :title="decisionTitle(trust)"
                            >
                                <icon-ph-check-bold v-if="trust.status === 'APPROVED'" class="w-3.5 h-3.5" aria-hidden="true" />
                                <icon-ph-x-bold v-else-if="trust.status === 'DECLINED' && !closedWithoutDecision(trust)" class="w-3.5 h-3.5" aria-hidden="true" />
                                {{ chipFor(trust).label }}
                            </span>
                        </div>
                    </li>
                </ul>
                <p
                    v-if="allDeclined"
                    data-test="trust-all-declined"
                    class="px-4 py-3 text-xs text-gray-600 dark:text-gray-300"
                >
                    Every trust declined, so the project stays staged until a decision changes or it is unstaged.
                </p>
            </div>
            <div v-if="anyDecidable" class="p-4 shrink-0 mt-auto">
                <div class="flex items-center justify-end w-full gap-4">
                    <AiButton
                        primary
                        small
                        data-test="approve-project-btn"
                        :disabled="!canSave"
                        :loading="approving"
                        @click="save"
                    >
                        Save Trust Decisions
                    </AiButton>
                </div>
            </div>
        </div>
    </AiCard>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import type { IProjectTrust, ITrustDecisions, TrustApprovalStatus } from "@/services/project-service";
import { useAuthStore } from "@/store/auth";
import { closedWithoutDecision } from "@/utils/trust-decisions";

type Decision = Exclude<TrustApprovalStatus, "PENDING">;

interface IProjectApprovalProps {
    approvedTrusts: IProjectTrust[];
    projectApproved: boolean;
    approving: boolean;
    canApprove: boolean;
}

const STATUS_CHIP: Record<TrustApprovalStatus, { label: string; class: string }> = {
    APPROVED: {
        label: "Approved",
        class: "bg-emerald-600 text-white"
    },
    DECLINED: {
        label: "Declined",
        class: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-200"
    },
    PENDING: {
        label: "Pending",
        class: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
    }
};

// A trust closed at upgrade was never declined (see closedWithoutDecision), so it is not shown as one.
const NOT_APPROVED_CHIP = {
    label: "Not approved",
    class: "bg-gray-100 text-gray-700 dark:bg-gray-800/60 dark:text-gray-200"
};

const chipFor = (trust: IProjectTrust) =>
    (closedWithoutDecision(trust) ? NOT_APPROVED_CHIP : STATUS_CHIP[trust.status]);

const CHOICE_BASE_CLASS =
    "relative inline-flex items-center gap-1 px-2 py-1 text-[11px] font-semibold border transition "
    + "focus:z-10 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 disabled:opacity-50";

const CHOICE_CLASS = {
    APPROVED: "bg-emerald-600 border-emerald-600 text-white",
    DECLINED: "bg-red-600 border-red-600 text-white",
    idle: "text-gray-700 dark:text-gray-200 bg-white border-gray-300 hover:bg-gray-50 "
        + "dark:bg-dark-surface dark:border-dark-border dark:hover:bg-dark-border"
} as const;

const authStore = useAuthStore();

const props = defineProps<IProjectApprovalProps>();

const emits = defineEmits<{ approveProject: [decisions: ITrustDecisions] }>();

const sortedTrusts = computed(() =>
    [...props.approvedTrusts].sort((a, b) => (a.code || a.name).localeCompare(b.code || b.name))
);

// The card is taller than its content on small rosters; the eyebrow answers
// "did everyone approve?" without scrolling the list.
const countLabel = computed(() => {
    const approved = props.approvedTrusts.filter(t => t.status === "APPROVED").length;
    const declined = props.approvedTrusts.filter(t => t.status === "DECLINED" && !closedWithoutDecision(t)).length;
    const label = `${approved} of ${props.approvedTrusts.length} approved`;

    return declined ? `${label} · ${declined} declined` : label;
});

const allDeclined = computed(() =>
    props.approvedTrusts.length > 0 && props.approvedTrusts.every(t => t.status === "DECLINED"));

const hasPermissionToApprove = computed(() => authStore.hasPermissions(["CanApproveProjects"]));

// Who may decide a trust (FLIP#1258): its own Trust Admin when it has one, otherwise the hub admin. While the
// project is STAGED a decision may still change; once it is APPROVED only a trust still pending can be decided.
const rowCanDecide = (trust: IProjectTrust): boolean => {
    if (!props.canApprove) return false;
    if (props.projectApproved && trust.status !== "PENDING") return false;

    return trust.hasTrustAdmin ? authStore.trustAdminOf?.id === trust.id : hasPermissionToApprove.value;
};

const decidableTrusts = computed(() => sortedTrusts.value.filter(rowCanDecide));
const anyDecidable = computed(() => decidableTrusts.value.length > 0);

const label = (trust: IProjectTrust) => trust.code || trust.name;

// The approver's unsaved choice per trust, starting from what is already recorded — so after every trust
// declined, the approver changes one decision rather than re-entering them all.
const choices = ref<Record<string, Decision>>({});

const savedStatuses = computed<Record<string, TrustApprovalStatus>>(() =>
    Object.fromEntries(props.approvedTrusts.map(t => [t.id, t.status])));

// The layout re-fetches the project every few seconds, handing this card a fresh array each time, so a
// trust's choice is reset only when its saved decision actually changed (a save, or another approver).
// Otherwise the approver's unsaved choice stands, however long they take.
watch(
    savedStatuses,
    (saved, previous) => {
        const next: Record<string, Decision> = {};
        for (const [id, status] of Object.entries(saved)) {
            if (previous?.[id] === status && choices.value[id]) next[id] = choices.value[id];
            else if (status !== "PENDING") next[id] = status;
        }
        choices.value = next;
    },
    { immediate: true }
);

// The trusts whose choice differs from what is recorded; only these are sent, so each trust is decided on its own.
const changedTrusts = computed(() =>
    decidableTrusts.value.filter(t => choices.value[t.id] && choices.value[t.id] !== t.status));

const canSave = computed(() => changedTrusts.value.length > 0 && !props.approving);

const choose = (trust: IProjectTrust, decision: Decision) => {
    choices.value = {
        ...choices.value,
        [trust.id]: decision
    };
};

const save = () => {
    if (!canSave.value) return;

    const idsWith = (decision: Decision) =>
        changedTrusts.value.filter(t => choices.value[t.id] === decision).map(t => t.id);
    emits("approveProject", {
        approved: idsWith("APPROVED"),
        declined: idsWith("DECLINED")
    });
};

const shortDate = (iso: string): string =>
    new Date(iso).toLocaleDateString(undefined, {
        day: "numeric",
        month: "short"
    });

// "Approved by Ada · 26 May", "Approved by Tia (UCH's Trust Admin) · 26 May". An approval recorded before
// decisions were attributed has a date but no decider.
const decisionLine = (trust: IProjectTrust): string => {
    if (trust.status === "PENDING") {
        return trust.hasTrustAdmin && !rowCanDecide(trust) ? `Awaiting ${label(trust)}'s Trust Admin` : "Awaiting decision";
    }
    if (closedWithoutDecision(trust)) return "No decision recorded";

    const site = trust.decidedAs === "SITE" ? ` (${label(trust)}'s Trust Admin)` : "";
    const by = trust.decidedByName ? ` by ${trust.decidedByName}${site}` : "";
    const on = trust.decidedAt ? ` · ${shortDate(trust.decidedAt)}` : "";

    return `${STATUS_CHIP[trust.status].label}${by}${on}`;
};

const decisionTitle = (trust: IProjectTrust): string => {
    if (trust.status === "PENDING") {
        return trust.hasTrustAdmin
            ? `${trust.name} is awaiting a decision by its Trust Admin`
            : `${trust.name} is awaiting a decision`;
    }
    if (closedWithoutDecision(trust)) return `${trust.name} was not approved; no decision was recorded`;

    const by = trust.decidedByName ? ` by ${trust.decidedByName}` : "";
    const on = trust.decidedAt ? ` on ${new Date(trust.decidedAt).toLocaleString()}` : "";

    return `${trust.name} ${trust.status.toLowerCase()}${by}${on}`;
};
</script>
