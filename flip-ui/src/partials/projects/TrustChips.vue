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
    <div class="flex-wrap items-center gap-1.5 min-w-0">
        <span
            v-for="trust in trustsToShow"
            :key="trust.id"
            class="inline-flex items-center py-0.5 rounded text-[11px] font-medium font-mono bg-gray-100 dark:bg-dark-surface text-gray-700 dark:text-gray-300"
            :class="dotFor(trust) ? 'gap-1.5 pl-1.5 pr-2' : 'px-2'"
            :title="chipTitle(trust)"
            data-test="trust-chip"
        >
            <span
                v-if="dotFor(trust)"
                class="w-1.5 h-1.5 rounded-full shrink-0"
                :class="dotFor(trust)?.class"
                data-test="trust-status-dot"
                aria-hidden="true"
            />
            {{ chipLabel(trust) }}
        </span>
        <span v-if="remainingTrustCount > 0" class="text-xs text-gray-500 dark:text-gray-300">
            +{{ remainingTrustCount }}
        </span>
        <span v-if="!sortedTrusts.length" class="text-xs text-gray-400 dark:text-gray-300 italic">
            No trusts staged
        </span>
    </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

import { IProject, IProjectTrust } from "@/services/project-service";

const props = defineProps<{ project: IProject }>();

// A chip's dot marks that trust's standing in the project. Colour and tooltip come
// from one row of this table so they can't disagree — the dot is aria-hidden and the
// two states differ only by hue, which colour-blind and screen-reader users can't
// read, so the tooltip is the accessible channel for the same distinction.
//
// `pending` is deliberately the same amber as the STAGED row spine in projects.vue
// (SPINE_BG_CLASS.STAGED): a staged row's dots and its spine should read as one
// colour. They are separate declarations, so the group-6 Cypress spec compares the
// two computed colours to catch a drift.
const TRUST_DOT = {
    in: {
        class: "bg-emerald-500",
        label: "approved"
    },
    pending: {
        class: "bg-amber-500",
        label: "awaiting approval"
    }
} as const;

// Emerald needs the project itself to have reached APPROVED as well as the trust to
// have signed off — it is the project-wide "this trust is in" marker. Anything short
// of that is pending, so on an APPROVED row a trust still awaiting sign-off stays
// amber against an emerald spine: deliberate, so "still pending" reads against its
// green neighbours. Drafts get no dot at all (`null` below) — their chips are merely
// linked trusts, with no standing to report yet.
//
// Status is allow-listed rather than tested with `!== "UNSTAGED"`: it arrives off the
// wire, where it can be absent or a value the union doesn't know yet, and an
// unrecognised status must not be painted as staged.
const dotFor = (trust: IProjectTrust): typeof TRUST_DOT[keyof typeof TRUST_DOT] | null => {
    if (props.project.status === "APPROVED") return trust.approved ? TRUST_DOT.in : TRUST_DOT.pending;
    if (props.project.status === "STAGED") return TRUST_DOT.pending;

    return null;
};

// `approvedTrusts` is every trust linked to the project (the name predates the
// staged/approved split) — each carries its own `approved` flag. We show them all so a
// freshly-staged trust appears straight away; the dot's colour, not chip presence,
// marks which trusts have approved. Sorted alphabetically so a trust keeps the same
// slot across reloads.
const sortedTrusts = computed<IProjectTrust[]>(() =>
    (props.project.approvedTrusts ?? []).slice().sort((a, b) => a.name.localeCompare(b.name)));

const MAX_TRUST_CHIPS = 4;
const trustsToShow = computed<IProjectTrust[]>(() => sortedTrusts.value.slice(0, MAX_TRUST_CHIPS));
const remainingTrustCount = computed<number>(() => Math.max(sortedTrusts.value.length - MAX_TRUST_CHIPS, 0));

// Prefer the trust's short code (e.g. "GSTT") when the backend supplies one. Fall back
// to the full name with common bloat ("NHS Foundation Trust" etc.) stripped and
// truncated to 16 chars so chips stay compact.
const chipLabel = (trust: IProjectTrust): string => {
    if (trust.code) return trust.code;
    if (!trust.name) return "";
    const stripped = trust.name
        .replace(/\bNHS Foundation Trust\b/gi, "")
        .replace(/\bNHS Trust\b/gi, "")
        .replace(/\bTrust\b/gi, "")
        .trim();
    if (stripped.length <= 16) return stripped;

    return stripped.slice(0, 14) + "…";
};

const chipTitle = (trust: IProjectTrust): string => {
    const dot = dotFor(trust);

    return dot ? `${trust.name} — ${dot.label}` : trust.name;
};
</script>
