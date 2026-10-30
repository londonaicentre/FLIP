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

<!-- The Trust Admin's auto-upgrade control on My Trust: the on/off switch, the optional maintenance window, the
     release the trust runs against the one the hub runs, why a pending upgrade has not started, and the last
     attempt. The page owns the fetch and the save; this card only edits a draft of the settings and emits it. -->
<template>
    <section data-test="auto-upgrade-card" class="p-6">
        <p class="text-[11px] font-mono uppercase tracking-widest text-gray-500 dark:text-gray-300">
            Automatic upgrades
        </p>

        <div class="flex items-center justify-between gap-4 mt-3">
            <div class="min-w-0">
                <h2 class="text-[15px] font-semibold text-gray-900 font-heading dark:text-gray-100">
                    Keep this Trust on the hub's release
                </h2>
                <p data-test="auto-upgrade-summary" class="mt-1 text-[12.5px] text-gray-600 dark:text-gray-300">
                    {{ draft.enabled
                        ? "On. This Trust upgrades itself when no training is running on it."
                        : "Off. An operator upgrades this Trust by hand." }}
                </p>
            </div>
            <Switch
                :model-value="draft.enabled"
                :disabled="saving"
                data-test="auto-upgrade-switch"
                aria-label="Automatic upgrades"
                class="relative inline-flex items-center h-6 transition-colors rounded-full shrink-0 w-11 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 dark:ring-offset-dark-canvas"
                :class="[
                    draft.enabled ? 'bg-primary-600 dark:bg-primary-400' : 'bg-gray-300 dark:bg-dark-raised',
                    saving && 'opacity-60 cursor-not-allowed'
                ]"
                @update:model-value="toggle"
            >
                <span
                    data-test="auto-upgrade-knob"
                    class="inline-block w-4 h-4 transition-transform transform bg-white rounded-full dark:bg-gray-200"
                    :class="draft.enabled ? 'translate-x-6' : 'translate-x-1'"
                />
            </Switch>
        </div>

        <dl class="grid grid-cols-2 gap-3 mt-5">
            <div class="px-3 py-2.5 border border-gray-100 rounded-lg bg-gray-50 dark:bg-dark-surface dark:border-dark-border">
                <dt class="font-mono text-[10.5px] uppercase tracking-[0.12em] text-gray-500 dark:text-gray-300">
                    This Trust runs
                </dt>
                <dd data-test="auto-upgrade-current" class="mt-1 font-mono text-sm font-semibold text-gray-900 dark:text-gray-100">
                    {{ autoUpgrade.currentRelease ?? "—" }}
                </dd>
            </div>
            <div class="px-3 py-2.5 border border-gray-100 rounded-lg bg-gray-50 dark:bg-dark-surface dark:border-dark-border">
                <dt class="font-mono text-[10.5px] uppercase tracking-[0.12em] text-gray-500 dark:text-gray-300">
                    The hub runs
                </dt>
                <dd data-test="auto-upgrade-target" class="mt-1 font-mono text-sm font-semibold text-gray-900 dark:text-gray-100">
                    {{ autoUpgrade.targetRelease ?? "—" }}
                </dd>
            </div>
        </dl>

        <!-- Status line: what the agent is doing about the gap, if any. -->
        <div
            data-test="auto-upgrade-status"
            :data-state="statusView.key"
            class="flex items-start gap-2 px-3 py-2.5 mt-3 text-[12.5px] font-semibold border rounded-lg"
            :class="statusView.classes"
        >
            <span aria-hidden="true">{{ statusView.icon }}</span>
            <span>{{ statusView.text }}</span>
        </div>
        <ul v-if="blockerTexts.length" data-test="auto-upgrade-blockers" class="mt-2 ml-1 space-y-1 text-[12.5px] text-gray-600 dark:text-gray-300">
            <li v-for="text in blockerTexts" :key="text" class="flex gap-2">
                <span aria-hidden="true">·</span>
                <span>{{ text }}</span>
            </li>
        </ul>

        <div v-if="autoUpgrade.lastAttempt" data-test="auto-upgrade-last-attempt" class="mt-4 text-[12.5px] text-gray-600 dark:text-gray-300">
            Last attempt:
            <span class="font-semibold" :class="autoUpgrade.lastAttempt.state === 'FAILED' ? 'text-red-700 dark:text-red-300' : ''">
                {{ autoUpgrade.lastAttempt.state === "FAILED" ? "failed" : "succeeded" }}
            </span>
            at <span class="font-mono">{{ autoUpgrade.lastAttempt.release }}</span>,
            {{ shortDate(autoUpgrade.lastAttempt.at) }}
            <pre
                v-if="autoUpgrade.lastAttempt.state === 'FAILED' && autoUpgrade.lastAttempt.detail"
                data-test="auto-upgrade-failure-detail"
                class="p-3 mt-2 overflow-x-auto font-mono text-[11px] text-gray-800 bg-gray-50 border border-gray-200 rounded-lg dark:bg-dark-surface dark:border-dark-border dark:text-gray-200"
            >{{ autoUpgrade.lastAttempt.detail }}</pre>
        </div>

        <!-- Maintenance window -->
        <fieldset class="pt-4 mt-5 border-t border-gray-200 dark:border-dark-border" :disabled="saving">
            <legend class="sr-only">
                Maintenance window
            </legend>
            <p class="text-[13px] font-semibold text-gray-900 dark:text-gray-100">
                Maintenance window
            </p>
            <p class="mt-1 text-[12.5px] text-gray-600 dark:text-gray-300">
                Upgrades start only inside this window, in this Trust's own time. Leave both times empty to allow any
                time of day.
            </p>
            <div class="grid grid-cols-2 gap-3 mt-3">
                <label class="text-[12px] text-gray-600 dark:text-gray-300">
                    From
                    <input
                        v-model="draft.windowStart"
                        data-test="auto-upgrade-window-start"
                        type="time"
                        class="block w-full mt-1 text-sm border-gray-300 rounded-md dark:bg-dark-surface dark:border-dark-border"
                    >
                </label>
                <label class="text-[12px] text-gray-600 dark:text-gray-300">
                    Until
                    <input
                        v-model="draft.windowEnd"
                        data-test="auto-upgrade-window-end"
                        type="time"
                        class="block w-full mt-1 text-sm border-gray-300 rounded-md dark:bg-dark-surface dark:border-dark-border"
                    >
                </label>
            </div>
            <label class="block mt-3 text-[12px] text-gray-600 dark:text-gray-300">
                Time zone
                <input
                    v-model="draft.timezone"
                    data-test="auto-upgrade-timezone"
                    type="text"
                    placeholder="Europe/London"
                    class="block w-full mt-1 text-sm border-gray-300 rounded-md dark:bg-dark-surface dark:border-dark-border"
                >
            </label>
            <p v-if="windowError" data-test="auto-upgrade-window-error" class="mt-2 text-[12.5px] text-red-700 dark:text-red-300">
                {{ windowError }}
            </p>
            <button
                type="button"
                data-test="auto-upgrade-save"
                class="px-3.5 py-2 mt-3 text-sm font-semibold text-white rounded-md bg-primary-600 hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed"
                :disabled="!dirty || !!windowError || saving"
                @click="emit('save', { ...draft })"
            >
                Save window
            </button>
        </fieldset>
    </section>
</template>

<script setup lang="ts">
import { Switch } from "@headlessui/vue";
import { computed, reactive, watch } from "vue";

import type { AutoUpgradeBlocker,
    AutoUpgradeState,
    IAutoUpgrade,
    IAutoUpgradeSettings } from "@/services/trust-service";
import { shortDate } from "@/utils/trust-decisions";

const props = defineProps<{
    autoUpgrade: IAutoUpgrade;
    saving?: boolean;
}>();

const emit = defineEmits<{
    (e: "save", settings: IAutoUpgradeSettings): void;
}>();

const fromServer = (a: IAutoUpgrade): IAutoUpgradeSettings => ({
    enabled: a.enabled,
    windowStart: a.windowStart,
    windowEnd: a.windowEnd,
    timezone: a.timezone
});

const draft = reactive<IAutoUpgradeSettings>(fromServer(props.autoUpgrade));

// A fresh answer from the hub (after a save, or the next poll) replaces the draft.
watch(() => props.autoUpgrade, (a) => {
    Object.assign(draft, fromServer(a));
});

// The switch saves at once; the window needs its own Save, because two half-typed times are not a window.
const toggle = (enabled: boolean) => {
    draft.enabled = enabled;
    emit("save", {
        ...fromServer(props.autoUpgrade),
        enabled
    });
};

const blank = (v: string | null) => (v ?? "") === "";
const dirty = computed(() => {
    const saved = fromServer(props.autoUpgrade);

    return (draft.windowStart || null) !== saved.windowStart
        || (draft.windowEnd || null) !== saved.windowEnd
        || (draft.timezone || null) !== saved.timezone;
});
const windowError = computed(() => {
    if (blank(draft.windowStart) !== blank(draft.windowEnd)) return "Set both the start and the end of the window, or clear both.";
    if (!blank(draft.windowStart) && blank(draft.timezone)) return "Name the Trust's time zone, e.g. Europe/London.";

    return "";
});

const BLOCKER_TEXT: Record<AutoUpgradeBlocker, string> = {
    JOBS_RUNNING: "A training job that uses this Trust is running. The upgrade waits for it to finish.",
    OUTSIDE_WINDOW: "It is outside the maintenance window. The upgrade starts when the window opens.",
    LOCAL_MODIFICATIONS: "The upgrade agent's copy of FLIP has local modifications. An operator must restore it.",
    SITE_UNHEALTHY: "This Trust is not healthy, so the upgrade is holding off until it is. See the status above.",
    AGENT_OFFLINE: "The upgrade agent on the Trust host has not checked in recently."
};
const blockerTexts = computed(() => (props.autoUpgrade.state === "PENDING" ? props.autoUpgrade.blockers : [])
    .map(b => BLOCKER_TEXT[b] ?? b));

interface IStatusView { key: string; icon: string; text: string; classes: string }
const NEUTRAL = "bg-gray-50 border-gray-200 text-gray-700 dark:bg-dark-surface dark:border-dark-border dark:text-gray-200";
const AMBER = "bg-amber-500/10 border-amber-500/30 text-amber-800 dark:text-amber-300";
const RED = "bg-red-500/[0.08] border-red-500/25 text-red-800 dark:text-red-300";
const GREEN = "bg-green-500/10 border-green-500/30 text-green-800 dark:text-green-300";

const statusView = computed<IStatusView>(() => {
    const a = props.autoUpgrade;
    const state: AutoUpgradeState = a.state;
    if (state === "FAILED") return {
        key: "failed",
        icon: "✕",
        classes: RED,
        text: "The last upgrade failed. Nothing is retried until an operator has checked the Trust."
    };
    if (state === "IN_PROGRESS") return {
        key: "in-progress",
        icon: "↻",
        classes: AMBER,
        text: `Upgrading to ${a.targetRelease ?? "the hub's release"}. New training jobs wait until it finishes.`
    };
    if (state === "PENDING" && a.enabled) return {
        key: "pending",
        icon: "⏳",
        classes: AMBER,
        text: `Upgrade to ${a.targetRelease ?? "the hub's release"} is waiting${a.pendingSince ? ` since ${shortDate(a.pendingSince)}` : ""}.`
    };
    if (state === "PENDING") return {
        key: "behind",
        icon: "⚠",
        classes: AMBER,
        text: `This Trust is behind the hub (${a.targetRelease ?? "newer release"}). Turn automatic upgrades on, or upgrade it by hand.`
    };

    return {
        key: "up-to-date",
        icon: "✓",
        classes: state === "SUCCEEDED" ? GREEN : NEUTRAL,
        text: "This Trust runs the hub's release."
    };
});
</script>
