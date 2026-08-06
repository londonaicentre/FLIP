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

<!-- Trust detail side drawer (design handoff option 1b, issue #901): per-container
     status / version / response for one trust, derived live from the same SWRV
     data the Connection Status table renders. Esc / scrim close come from the
     HeadlessUI Dialog; focus returns to the previously-focused element (the
     trigger rows are tabindex=0, so it lands back on the row). -->
<template>
    <TransitionRoot as="template" :show="show">
        <Dialog as="div" class="fixed inset-0 z-10 overflow-hidden" :unmount="true" @close="emit('close')">
            <div class="absolute inset-0 overflow-hidden">
                <TransitionChild
                    as="template"
                    enter="ease-out duration-[250ms]"
                    enter-from="opacity-0"
                    enter-to="opacity-100"
                    leave="ease-out duration-[250ms]"
                    leave-from="opacity-100"
                    leave-to="opacity-0"
                >
                    <AiDialogOverlay />
                </TransitionChild>

                <!-- The panel div below must be the TransitionChild's single, unconditional
                     child (no v-if, no sibling comment nodes): HeadlessUI forwards props onto
                     exactly one element, and the page nulls `trust` in the same tick it flips
                     `show` off — a conditional panel would hand the leave transition a comment
                     vnode and throw. Content is guarded inside the panel instead. -->
                <div class="fixed inset-y-0 right-0 flex max-w-full pl-10">
                    <TransitionChild
                        as="template"
                        enter="transform transition ease-out duration-[250ms]"
                        enter-from="translate-x-full"
                        enter-to="translate-x-0"
                        leave="transform transition ease-out duration-[250ms]"
                        leave-from="translate-x-0"
                        leave-to="translate-x-full"
                    >
                        <div
                            data-test="drawer-panel"
                            class="w-screen max-w-[410px] flex flex-col h-full bg-white dark:bg-dark-surface
                            border-l border-gray-200 dark:border-dark-border shadow-2xl dark:ring-1 dark:ring-white/20"
                        >
                            <template v-if="displayTrust">
                                <!-- Header -->
                                <div class="px-[22px] pt-[22px] pb-[18px] border-b border-gray-200 dark:border-dark-border">
                                    <div class="flex items-start justify-between gap-3">
                                        <div class="min-w-0">
                                            <p class="text-[11px] font-mono uppercase tracking-widest text-gray-500 dark:text-gray-300">
                                                Trust detail
                                            </p>
                                            <DialogTitle
                                                class="font-heading font-semibold text-[19px] mt-1 text-gray-900 dark:text-gray-100 truncate"
                                            >
                                                {{ displayTrust.name }}
                                            </DialogTitle>
                                            <p class="font-mono text-xs text-gray-600 dark:text-gray-300 mt-0.5 truncate">
                                                {{ codeAndRegion }}
                                            </p>
                                        </div>
                                        <button
                                            type="button"
                                            data-test="drawer-close"
                                            class="shrink-0 opacity-70 hover:opacity-100 transition rounded
                                            text-gray-600 dark:text-gray-300 focus:outline-none focus:ring-1
                                            focus:ring-primary-400"
                                            tabindex="0"
                                            @click="emit('close')"
                                        >
                                            <span class="sr-only">Close</span>
                                            <icon-heroicons-outline-x class="w-5 h-5" />
                                        </button>
                                    </div>
                                    <div class="flex items-center gap-3 mt-[14px]">
                                        <span
                                            class="inline-block px-2 py-0.5 rounded text-xs font-medium"
                                            :class="PILL_CLASSES[state]"
                                        >
                                            {{ STATE_LABELS[state] }}
                                        </span>
                                        <span
                                            data-test="drawer-heartbeat"
                                            class="font-mono text-xs"
                                            :class="state === 'offline'
                                                ? 'text-red-600 dark:text-red-400'
                                                : 'text-gray-600 dark:text-gray-300'"
                                        >
                                            heartbeat {{ heartbeatText(displayTrust.last_heartbeat) }}
                                        </span>
                                    </div>
                                </div>

                                <!-- Issue banner -->
                                <div v-if="state !== 'online'" class="px-[14px] pt-[14px]">
                                    <div
                                        data-test="drawer-banner"
                                        class="flex items-start gap-2 rounded-lg border px-3 py-2.5 text-[12.5px] font-semibold"
                                        :class="state === 'offline'
                                            ? 'bg-red-500/[0.08] border-red-500/25 text-red-800 dark:text-red-300'
                                            : 'bg-amber-500/10 border-amber-500/30 text-amber-800 dark:text-amber-300'"
                                    >
                                        <span aria-hidden="true">⚠</span>
                                        <span>{{ bannerText }}</span>
                                    </div>
                                </div>

                                <!-- Container list -->
                                <div class="flex-1 overflow-y-auto px-[22px] pb-[22px] pt-2">
                                    <p class="text-[11px] font-mono uppercase tracking-widest text-gray-500 dark:text-gray-300 pt-4 pb-1">
                                        Containers · {{ services.length }}
                                    </p>
                                    <div
                                        v-for="svc in services"
                                        :key="svc.key"
                                        data-test="container-row"
                                        :data-service="svc.key"
                                        class="flex items-start gap-3 py-[13px] border-t border-gray-200 dark:border-dark-border"
                                    >
                                        <component
                                            :is="SERVICE_ICONS[svc.key]"
                                            class="w-[22px] h-[22px] mt-0.5 shrink-0"
                                            :class="ICON_TINT[svc.status]"
                                        />
                                        <div class="flex-1 min-w-0">
                                            <div class="flex items-center justify-between gap-2">
                                                <span class="font-mono text-[13px] font-semibold text-gray-900 dark:text-gray-100 truncate">
                                                    {{ svc.label }}
                                                </span>
                                                <span
                                                    data-test="container-chip"
                                                    class="shrink-0 px-2 py-0.5 rounded-full text-[11px] font-bold whitespace-nowrap"
                                                    :class="CHIP_CLASSES[svc.status]"
                                                >
                                                    {{ CHIP_LABELS[svc.status] }}
                                                </span>
                                            </div>
                                            <p class="text-[11.5px] text-gray-600 dark:text-gray-300 mt-0.5">
                                                {{ svc.role }}
                                            </p>
                                            <p data-test="container-meta" class="font-mono text-[11px] text-gray-500 dark:text-gray-300 mt-1.5">
                                                <span>{{ svc.version ?? "—" }}</span>
                                                <span class="mx-2 text-gray-300 dark:text-gray-600">·</span>
                                                <span
                                                    class="font-semibold"
                                                    :class="svc.status === 'degraded' ? 'text-amber-700 dark:text-amber-400' : ''"
                                                >{{ svc.response_ms !== null ? `${svc.response_ms} ms` : "—" }}</span>
                                            </p>
                                        </div>
                                    </div>
                                </div>
                            </template>
                        </div>
                    </TransitionChild>
                </div>
            </div>
        </Dialog>
    </TransitionRoot>
</template>

<script setup lang="ts">
import { Dialog, DialogTitle, TransitionChild, TransitionRoot } from "@headlessui/vue";
import { computed, type FunctionalComponent, ref, watch } from "vue";

import AiDialogOverlay from "@/components/AiDialogOverlay/AiDialogOverlay.vue";
import { ITrustResponse, ServiceStatus } from "@/services/trust-service";
import { deriveServices,
    deriveTrustState,
    heartbeatText,
    PILL_CLASSES,
    ServiceKey,
    STATE_LABELS,
    TrustState } from "@/utils/connection-health";
import IconCube from "~icons/ph/cube-duotone";
import IconDatabase from "~icons/ph/database-duotone";
import IconHardDrives from "~icons/ph/hard-drives-duotone";
import IconImages from "~icons/ph/images-duotone";
import IconPlugsConnected from "~icons/ph/plugs-connected-duotone";
import IconStack from "~icons/ph/stack-duotone";

const props = defineProps<{
    trust: ITrustResponse | null;
    show: boolean;
}>();

const emit = defineEmits<{ close: [] }>();

const CHIP_LABELS: Record<ServiceStatus, string> = {
    healthy: "Healthy",
    degraded: "Degraded",
    down: "Down",
    unknown: "No data"
};
const CHIP_CLASSES: Record<ServiceStatus, string> = {
    healthy: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-100",
    degraded: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
    down: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200",
    unknown: "bg-gray-100 text-gray-600 dark:bg-gray-800/60 dark:text-gray-300"
};
const ICON_TINT: Record<ServiceStatus, string> = {
    healthy: "text-emerald-600",
    degraded: "text-amber-500",
    down: "text-red-500",
    unknown: "text-gray-400 dark:text-gray-300"
};

// Duotone glyph per registry key (design handoff icon table). Keyed by ServiceKey
// so adding a registry service without an icon fails to compile.
const SERVICE_ICONS: Record<ServiceKey, FunctionalComponent> = {
    "trust-api": IconPlugsConnected,
    xnat: IconImages,
    "imaging-api": IconCube,
    omop: IconDatabase,
    dicom: IconHardDrives,
    "data-access-api": IconStack
};

// The page nulls `trust` in the same tick it flips `show` off; keep the last
// non-null trust so the 250ms slide-out renders content, not an empty shell.
const lastTrust = ref<ITrustResponse | null>(null);
watch(
    () => props.trust,
    t => {
        if (t) lastTrust.value = t;
    },
    { immediate: true }
);
const displayTrust = computed(() => props.trust ?? lastTrust.value);

const codeAndRegion = computed(() =>
    [displayTrust.value?.code, displayTrust.value?.region].filter(Boolean).join(" · "));

const services = computed(() => (displayTrust.value ? deriveServices(displayTrust.value) : []));
const state = computed<TrustState>(() => (displayTrust.value ? deriveTrustState(displayTrust.value) : "offline"));

const bannerText = computed(() => {
    if (state.value === "offline") {
        return "Core trust-api is unreachable — no data can be collected from this Trust.";
    }
    const affected = services.value.filter(s => s.status === "down" || s.status === "degraded").length;

    return `${affected} ${affected === 1 ? "container" : "containers"} affecting service.`;
});
</script>
