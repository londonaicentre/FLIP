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
    name: Connection Status (Trusts)
</route>

<template>
    <div class="flex flex-col w-full h-full">
        <div class="w-full px-8 pt-8 pb-8 overflow-y-auto">
            <!-- Header — design ref: ConnectionA (CSHeader) in design_handoff_full/05_connection/connection.jsx -->
            <div class="flex flex-col gap-4 mb-4 sm:flex-row sm:items-end sm:justify-between">
                <div>
                    <p class="text-xs font-mono uppercase tracking-widest text-gray-500 dark:text-gray-400">
                        Federation · {{ trusts?.length ?? 0 }} {{ (trusts?.length ?? 0) === 1 ? "trust" : "trusts" }}
                    </p>
                    <h1 class="text-3xl font-semibold font-heading mt-1 text-gray-900 dark:text-gray-100">
                        <span class="text-primary-600 underline decoration-4 decoration-primary-500/60 underline-offset-8 dark:text-white">Connection</span>
                        <span class="ml-2">status (Trusts)</span>
                    </h1>
                    <p class="mt-2 text-sm text-gray-500 dark:text-gray-400" data-test="subtitle">
                        {{ subtitle }}
                    </p>
                </div>
                <div class="flex-shrink-0 sm:pb-4">
                    <AiButton
                        v-if="isAdmin"
                        primary
                        large
                        data-test="add-trust-btn"
                        @click="showAddTrustModal = true"
                    >
                        Add Trust
                    </AiButton>
                </div>
            </div>

            <!-- View toggle: list (ConnectionA) vs radial (ConnectionD) -->
            <div class="flex items-center justify-end mb-3 gap-2">
                <span class="text-[11px] font-mono uppercase tracking-widest text-gray-500 dark:text-gray-400">View</span>
                <div
                    role="tablist"
                    aria-label="Connection view"
                    class="inline-flex bg-gray-100 dark:bg-gray-800 rounded-lg p-1 ring-1 ring-black/5 dark:ring-white/10"
                >
                    <button
                        type="button"
                        role="tab"
                        :aria-selected="viewMode === 'list'"
                        data-test="view-toggle-list"
                        class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all"
                        :class="viewMode === 'list'
                            ? 'bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 shadow-sm'
                            : 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'"
                        @click="viewMode = 'list'"
                    >
                        <icon-ph-list-bullets-duotone class="w-4 h-4" />
                        List
                    </button>
                    <button
                        type="button"
                        role="tab"
                        :aria-selected="viewMode === 'radial'"
                        data-test="view-toggle-radial"
                        class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all"
                        :class="viewMode === 'radial'
                            ? 'bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 shadow-sm'
                            : 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'"
                        @click="viewMode = 'radial'"
                    >
                        <icon-ph-share-network-duotone class="w-4 h-4" />
                        Topology
                    </button>
                </div>
            </div>

            <!-- Summary card (CSSummary) -->
            <AiCard v-if="viewMode === 'list'" class="mb-4">
                <div class="flex flex-wrap">
                    <div
                        v-for="(s, idx) in summaryCells"
                        :key="s.key"
                        class="flex flex-col gap-1 px-6 py-4 min-w-[140px]"
                        :class="{ 'border-l border-gray-200 dark:border-gray-700': idx > 0 }"
                    >
                        <div class="flex items-center gap-2">
                            <span class="inline-block w-2 h-2 rounded-full" :class="s.dotClass" />
                            <span class="text-[11px] font-mono uppercase tracking-widest text-gray-500 dark:text-gray-400">
                                {{ s.label }}
                            </span>
                        </div>
                        <div
                            class="font-heading font-semibold text-3xl leading-none"
                            :class="s.count > 0 && s.key !== 'online' ? s.warnTextClass : 'text-gray-900 dark:text-gray-100'"
                        >
                            {{ s.count }}
                        </div>
                    </div>
                </div>
            </AiCard>

            <!-- Status table (ConnectionA table) -->
            <AiCard v-if="viewMode === 'list'">
                <div v-if="!trusts" class="p-8 text-center">
                    <AiLoader />
                </div>
                <div v-else class="overflow-x-auto">
                    <table class="w-full">
                        <thead>
                            <tr class="bg-gray-50 dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
                                <th
                                    v-for="col in columns"
                                    :key="col.key ?? col.label"
                                    :data-test="col.key ? `sort-header-${col.key}` : undefined"
                                    class="px-4 py-3 text-[11px] font-mono uppercase tracking-widest text-gray-500 font-medium select-none"
                                    :class="[
                                        col.align === 'right' ? 'text-right' : 'text-left',
                                        col.key ? 'cursor-pointer hover:text-gray-700 dark:hover:text-gray-300' : ''
                                    ]"
                                    @click="col.key ? toggleSort(col.key) : undefined"
                                >
                                    {{ col.label }}
                                    <span
                                        v-if="col.key && sortKey === col.key"
                                        class="ml-1 text-primary-600 dark:text-primary-300"
                                    >{{ sortDir === "asc" ? "↑" : "↓" }}</span>
                                </th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr
                                v-for="(t, idx) in sortedTrusts"
                                :key="t.id"
                                data-test="trust-row"
                                :class="[
                                    idx > 0 ? 'border-t border-gray-100 dark:border-gray-700' : '',
                                    t._state === 'offline' ? 'bg-red-50/40 dark:bg-red-900/10' : ''
                                ]"
                            >
                                <td class="px-4 py-4 align-middle">
                                    <div class="inline-flex items-center gap-2">
                                        <span class="inline-block w-2 h-2 rounded-full" :class="dotClass(t._state)" />
                                        <span
                                            class="inline-block px-2 py-0.5 rounded text-[11px] font-medium"
                                            :class="pillClass(t._state)"
                                        >
                                            {{ stateLabel(t._state) }}
                                        </span>
                                    </div>
                                </td>
                                <td class="px-4 py-4 align-middle">
                                    <div class="flex flex-col">
                                        <span class="font-semibold font-heading text-sm text-gray-900 dark:text-gray-100">
                                            {{ t.code || t.name }}
                                        </span>
                                        <span
                                            v-if="t.code && t.code !== t.name"
                                            class="font-mono text-[11px] text-gray-500 dark:text-gray-400 mt-0.5"
                                            data-test="trust-name"
                                        >
                                            {{ t.name }}
                                        </span>
                                    </div>
                                </td>
                                <td class="px-4 py-4 align-middle text-sm text-gray-600 dark:text-gray-400">
                                    {{ t.region ?? "—" }}
                                </td>
                                <td
                                    class="px-4 py-4 align-middle font-mono text-xs"
                                    :class="t._state === 'offline' ? 'text-red-600 dark:text-red-400' : 'text-gray-600 dark:text-gray-400'"
                                    data-test="trust-heartbeat"
                                >
                                    {{ heartbeatText(t.last_heartbeat) }}
                                </td>
                                <td class="px-4 py-4 align-middle text-right font-mono text-sm text-gray-900 dark:text-gray-100">
                                    {{ t.project_count }}
                                </td>
                                <td class="px-4 py-4 align-middle" data-test="trust-uptime">
                                    <svg
                                        :width="uptimeSvg.w"
                                        :height="uptimeSvg.h"
                                        class="block"
                                        :title="uptimeTitle"
                                    >
                                        <polyline
                                            :points="t._uptimePoints"
                                            fill="none"
                                            :stroke="uptimeStroke(t._state)"
                                            stroke-width="1.6"
                                            stroke-linejoin="round"
                                            stroke-linecap="round"
                                        />
                                        <circle
                                            v-for="(pt, i) in t._uptimeDots"
                                            :key="i"
                                            :cx="pt.x"
                                            :cy="pt.y"
                                            r="1.6"
                                            :fill="uptimeStroke(t._state)"
                                            :opacity="i === t._uptimeDots.length - 1 ? 1 : 0.35"
                                        />
                                    </svg>
                                </td>
                                <td class="px-4 py-4" />
                            </tr>
                            <tr v-if="!sortedTrusts.length">
                                <td colspan="7" class="text-center py-6 text-gray-500 dark:text-gray-400">
                                    No trusts registered yet.
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </AiCard>

            <!-- Radial view (ConnectionD) — design ref: design_handoff_full/05_connection/connection-2.jsx -->
            <div v-else class="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-4 items-stretch">
                <AiCard
                    class="relative overflow-hidden h-[560px] connection-topology-card"
                >
                    <div class="absolute top-4 left-5 z-10">
                        <div class="text-[11px] font-mono uppercase tracking-widest text-gray-500 dark:text-gray-400">
                            Federation topology · Live
                        </div>
                        <div class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                            {{ trusts?.length ?? 0 }}
                            {{ (trusts?.length ?? 0) === 1 ? "trust" : "trusts" }} connected to FLIP
                        </div>
                    </div>

                    <div class="absolute top-4 right-5 z-10 flex gap-3.5 items-center bg-white dark:bg-gray-900 px-3 py-2 border border-gray-200 dark:border-gray-700 rounded-lg">
                        <span
                            v-for="legend in radialLegend"
                            :key="legend.state"
                            class="inline-flex items-center gap-1.5 text-[11px] text-gray-700 dark:text-gray-300"
                        >
                            <span class="inline-block w-2 h-2 rounded-full" :class="legend.dotClass" />
                            {{ legend.label }}
                        </span>
                    </div>

                    <div v-if="!trusts" class="absolute inset-0 flex items-center justify-center">
                        <AiLoader />
                    </div>

                    <svg
                        v-else
                        viewBox="0 0 760 760"
                        preserveAspectRatio="xMidYMid meet"
                        class="w-full h-full block"
                        data-test="connection-radial-svg"
                    >
                        <defs>
                            <radialGradient id="hub-glow" cx="50%" cy="50%" r="50%">
                                <stop offset="0%" stop-color="#61366E" stop-opacity="0.5" />
                                <stop offset="60%" stop-color="#61366E" stop-opacity="0.08" />
                                <stop offset="100%" stop-color="#61366E" stop-opacity="0" />
                            </radialGradient>
                            <linearGradient
                                id="link-pulse"
                                x1="0%"
                                y1="0%"
                                x2="100%"
                                y2="0%"
                            >
                                <stop offset="0%" stop-color="#a78bfa" stop-opacity="0.1" />
                                <stop offset="50%" stop-color="#d946ef" stop-opacity="0.9" />
                                <stop offset="100%" stop-color="#a78bfa" stop-opacity="0.1" />
                                <animate attributeName="x1" values="-100%;100%" dur="2.4s" repeatCount="indefinite" />
                                <animate attributeName="x2" values="0%;200%" dur="2.4s" repeatCount="indefinite" />
                            </linearGradient>
                        </defs>

                        <!-- Concentric ring guides -->
                        <circle
                            v-for="(m, i) in [0.65, 0.85, 1.0]"
                            :key="`ring-${i}`"
                            :cx="radial.cx"
                            :cy="radial.cy"
                            :r="radial.R * m"
                            fill="none"
                            stroke="#e5e7eb"
                            :stroke-width="i === 2 ? 1 : 0.6"
                            :stroke-dasharray="i === 2 ? 'none' : '2 4'"
                        />

                        <!-- Hub glow -->
                        <circle :cx="radial.cx" :cy="radial.cy" r="120" fill="url(#hub-glow)" />

                        <!-- Spokes -->
                        <g>
                            <g v-for="node in radialNodes" :key="`spoke-${node.id}`">
                                <line
                                    :x1="radial.cx"
                                    :y1="radial.cy"
                                    :x2="node.x"
                                    :y2="node.y"
                                    :stroke="node.state === 'offline' ? '#ef4444' : DOT_HEX[node.state]"
                                    :stroke-width="node.state === 'online' ? 1.4 : 0.8"
                                    :stroke-opacity="node.state === 'offline' ? 0.4 : node.state === 'online' ? 0.5 : 0.2"
                                    :stroke-dasharray="node.state === 'offline' ? '3 4' : 'none'"
                                />
                                <line
                                    v-if="node.state === 'online'"
                                    :x1="radial.cx"
                                    :y1="radial.cy"
                                    :x2="node.x"
                                    :y2="node.y"
                                    stroke="url(#link-pulse)"
                                    stroke-width="2.6"
                                    stroke-linecap="round"
                                />
                            </g>
                        </g>

                        <!-- Hub at centre: FLIP logo on a white disc, animated halo -->
                        <g>
                            <circle
                                :cx="radial.cx"
                                :cy="radial.cy"
                                r="44"
                                fill="none"
                                stroke="#61366E"
                                stroke-opacity="0.25"
                                stroke-width="1"
                            >
                                <animate attributeName="r" values="44;64;44" dur="2.2s" repeatCount="indefinite" />
                                <animate attributeName="stroke-opacity" values="0.5;0;0.5" dur="2.2s" repeatCount="indefinite" />
                            </circle>
                            <circle
                                :cx="radial.cx"
                                :cy="radial.cy"
                                r="40"
                                fill="#ffffff"
                                stroke="#61366E"
                                stroke-width="3"
                            />
                            <image
                                href="/images/flip-logo-icon.png"
                                :x="radial.cx - 28"
                                :y="radial.cy - 28"
                                width="56"
                                height="56"
                                preserveAspectRatio="xMidYMid meet"
                            />
                        </g>

                        <!-- Trust nodes + labels -->
                        <g>
                            <g
                                v-for="node in radialNodes"
                                :key="`node-${node.id}`"
                                style="cursor: pointer;"
                                @mouseenter="hoverTrustId = node.id"
                                @mouseleave="hoverTrustId = null"
                            >
                                <circle
                                    v-if="hoverTrustId === node.id"
                                    :cx="node.x"
                                    :cy="node.y"
                                    :r="node.r + 8"
                                    :fill="DOT_HEX[node.state]"
                                    opacity="0.18"
                                />
                                <circle
                                    :cx="node.x"
                                    :cy="node.y"
                                    :r="node.r"
                                    fill="#ffffff"
                                    :stroke="DOT_HEX[node.state]"
                                    stroke-width="3"
                                />
                                <circle :cx="node.x" :cy="node.y" :r="node.r - 4" :fill="DOT_HEX[node.state]" />
                                <g v-if="node.state === 'offline'">
                                    <line
                                        :x1="node.x - node.r * 0.4"
                                        :y1="node.y - node.r * 0.4"
                                        :x2="node.x + node.r * 0.4"
                                        :y2="node.y + node.r * 0.4"
                                        stroke="#fff"
                                        stroke-width="1.6"
                                    />
                                    <line
                                        :x1="node.x - node.r * 0.4"
                                        :y1="node.y + node.r * 0.4"
                                        :x2="node.x + node.r * 0.4"
                                        :y2="node.y - node.r * 0.4"
                                        stroke="#fff"
                                        stroke-width="1.6"
                                    />
                                </g>
                                <text
                                    :x="node.labelX"
                                    :y="node.labelY + 1"
                                    :text-anchor="node.anchor"
                                    font-family="ui-monospace, monospace"
                                    font-size="11"
                                    font-weight="600"
                                    fill="#374151"
                                    letter-spacing="0.04em"
                                >
                                    {{ node.shortLabel }}
                                </text>
                                <text
                                    :x="node.labelX"
                                    :y="node.labelY + 14"
                                    :text-anchor="node.anchor"
                                    font-size="10"
                                    fill="#6b7280"
                                >
                                    {{ node.project_count }} {{ node.project_count === 1 ? "project" : "projects" }}
                                </text>
                            </g>
                        </g>
                    </svg>

                    <!-- Live count: online trusts -->
                    <div class="absolute bottom-4 left-5 z-10 flex gap-4 items-center">
                        <span class="inline-flex items-center gap-2">
                            <span class="inline-block w-2 h-2 rounded-full bg-fuchsia-500 animate-pulse" />
                            <span class="font-mono text-[11px] uppercase tracking-widest text-gray-700 dark:text-gray-300">
                                {{ onlineCount }} online
                            </span>
                        </span>
                    </div>
                </AiCard>

                <!-- Side panel: incidents + hover detail -->
                <AiCard class="p-0 flex flex-col overflow-hidden">
                    <div class="px-5 py-4 border-b border-gray-200 dark:border-gray-700">
                        <div class="text-[11px] font-mono uppercase tracking-widest text-gray-500 dark:text-gray-400">
                            Incidents · open
                        </div>
                    </div>
                    <div v-if="incidents.length" data-test="radial-incidents">
                        <div
                            v-for="t in incidents"
                            :key="t.id"
                            class="px-5 py-3.5 border-b border-gray-100 dark:border-gray-700 flex flex-col gap-1.5"
                        >
                            <div class="flex justify-between items-center">
                                <span class="font-heading font-semibold text-[13px] text-gray-900 dark:text-gray-100">
                                    {{ t.code || t.name }}
                                </span>
                                <span class="inline-block px-2 py-0.5 rounded text-[11px] font-medium" :class="pillClass(t._state)">
                                    {{ stateLabel(t._state) }}
                                </span>
                            </div>
                            <span
                                class="text-xs"
                                :class="t._state === 'offline' ? 'text-red-600 dark:text-red-400' : 'text-amber-600 dark:text-amber-400'"
                            >
                                {{ t._state === "offline" ? "Unreachable" : "Heartbeat" }} ·
                                {{ heartbeatText(t.last_heartbeat) }}
                            </span>
                        </div>
                    </div>
                    <div v-else class="px-5 py-4 text-xs text-gray-500 dark:text-gray-400">
                        No open incidents.
                    </div>

                    <div class="px-5 py-4 border-t border-gray-200 dark:border-gray-700 mt-auto">
                        <div class="text-[11px] font-mono uppercase tracking-widest text-gray-500 dark:text-gray-400">
                            Trust detail
                        </div>
                    </div>
                    <div
                        v-if="hoveredTrust"
                        class="px-5 py-3 flex flex-col gap-1 border-t border-gray-100 dark:border-gray-700"
                        data-test="radial-hover-detail"
                    >
                        <span class="font-heading font-semibold text-[13px] text-gray-900 dark:text-gray-100">
                            {{ hoveredTrust.code || hoveredTrust.name }}
                        </span>
                        <span class="font-mono text-[11px] text-gray-500 dark:text-gray-400">
                            {{ hoveredTrust.region ?? "—" }} · {{ heartbeatText(hoveredTrust.last_heartbeat) }}
                        </span>
                    </div>
                    <div
                        v-else
                        class="px-5 py-3 text-[11px] text-gray-400 dark:text-gray-500 border-t border-gray-100 dark:border-gray-700"
                    >
                        Hover a trust on the topology to see details.
                    </div>
                </AiCard>
            </div>
        </div>
    </div>

    <AddTrustModal
        :dialog="showAddTrustModal"
        @close-modal="showAddTrustModal = false"
        @on-success="onTrustCreated"
    />

    <TrustKitModal
        :dialog="!!createdTrust"
        :trust="createdTrust"
        @close-modal="onKitModalClose"
    />
</template>

<script setup lang="ts">
import useSWRV from "swrv";
import { computed, ref } from "vue";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiCard from "@/components/AiCard/AiCard.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import AddTrustModal from "@/partials/trusts/AddTrustModal.vue";
import TrustKitModal from "@/partials/trusts/TrustKitModal.vue";
import { getAdminTrusts, IAdminTrust, ICreatedTrust } from "@/services/admin-trusts-service";
import { useAuthStore } from "@/store/auth";

const authStore = useAuthStore();
const isAdmin = computed(() => authStore.hasPermissions(["CanAccessAdminPanel"]));

const showAddTrustModal = ref(false);
const createdTrust = ref<ICreatedTrust | null>(null);

type ViewMode = "list" | "radial";
const viewMode = ref<ViewMode>("list");
const hoverTrustId = ref<string | null>(null);

// SWRV handles 5s dedupe + 15s polling so the heartbeat column stays fresh
// without per-page setInterval bookkeeping. Same pattern as ConnectionStatus.vue.
const { data: trusts, mutate: refresh } = useSWRV<IAdminTrust[]>(
    "/admin/trusts",
    getAdminTrusts,
    {
        dedupingInterval: 5_000,
        shouldRetryOnError: false,
        refreshInterval: 15_000
    }
);

type TrustState = "online" | "degraded" | "offline" | "disabled";

// A heartbeat newer than HEARTBEAT_FRESH_S is "online"; older than HEARTBEAT_DEGRADED_S
// (or absent) is "offline"; in between is "degraded". Trust-side poll interval
// defaults to 5s, so 30s/300s gives ~6×/60× the poll cadence.
const HEARTBEAT_FRESH_S = 30;
const HEARTBEAT_DEGRADED_S = 5 * 60;

const trustState = (t: IAdminTrust): TrustState => {
    if (t.disabled_at) return "disabled";
    if (!t.last_heartbeat) return "offline";
    const ageS = (Date.now() - new Date(t.last_heartbeat).getTime()) / 1000;
    if (ageS < HEARTBEAT_FRESH_S) return "online";
    if (ageS < HEARTBEAT_DEGRADED_S) return "degraded";

    return "offline";
};

const STATE_LABELS: Record<TrustState, string> = {
    online: "Online",
    degraded: "Degraded",
    offline: "Offline",
    disabled: "Disabled"
};
const stateLabel = (s: TrustState): string => STATE_LABELS[s];

const DOT_CLASSES: Record<TrustState, string> = {
    online: "bg-emerald-600",
    degraded: "bg-amber-500",
    offline: "bg-red-500",
    disabled: "bg-gray-400"
};
const dotClass = (s: TrustState): string => DOT_CLASSES[s];

const PILL_CLASSES: Record<TrustState, string> = {
    online: "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-100",
    degraded: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
    offline: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200",
    disabled: "bg-gray-200 text-gray-700 dark:bg-gray-700 dark:text-gray-300"
};
const pillClass = (s: TrustState): string => PILL_CLASSES[s];

const heartbeatText = (iso: string | null): string => {
    if (!iso) return "never";
    const sec = (Date.now() - new Date(iso).getTime()) / 1000;
    if (sec < 60) return `${Math.max(0, Math.floor(sec))}s ago`;
    if (sec < 3_600) return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86_400) return `${Math.floor(sec / 3_600)}h ago`;

    return `${Math.floor(sec / 86_400)}d ago`;
};

// Placeholder until backend uptime tracking lands (see issue #506 follow-up:
// historical heartbeat storage). The shape is real — 7 daily samples in
// [0, 1] — but values are derived from the trust's current state so the
// sparkline renders consistently across reloads.
const UPTIME_BY_STATE: Record<TrustState, number[]> = {
    online: [1, 1, 1, 1, 1, 1, 1],
    degraded: [1, 1, 0.4, 1, 1, 1, 0.7],
    offline: [1, 1, 1, 0.5, 0, 0, 0],
    disabled: [0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4]
};
const uptimeSvg = {
    w: 72,
    h: 22
};
const uptimeTitle = "7-day uptime (placeholder — backend tracking pending)";

const STROKE_BY_STATE: Record<TrustState, string> = {
    online: "#059669",
    degraded: "#f59e0b",
    offline: "#ef4444",
    disabled: "#9ca3af"
};
const uptimeStroke = (s: TrustState): string => STROKE_BY_STATE[s];

const STATE_RANK: Record<TrustState, number> = {
    offline: 0,
    degraded: 1,
    disabled: 2,
    online: 3
};

interface IRenderedTrust extends IAdminTrust {
    _state: TrustState;
    _uptimePoints: string;
    _uptimeDots: { x: number; y: number }[];
}

type SortKey = "severity" | "name" | "region" | "heartbeat" | "projects";
type SortDir = "asc" | "desc";

interface IColumn {
    key?: SortKey;
    label: string;
    align?: "left" | "right";
}

// Header config drives both the rendered <th>s and the per-column sort handler.
// Keys correspond to entries in `sortComparators`; columns without a key
// (sparkline, action) render as inert headers.
const columns: IColumn[] = [
    {
        key: "severity",
        label: "Status"
    },
    {
        key: "name",
        label: "Trust"
    },
    {
        key: "region",
        label: "Region"
    },
    {
        key: "heartbeat",
        label: "Heartbeat"
    },
    {
        key: "projects",
        label: "Projects",
        align: "right"
    },
    { label: "7d uptime" },
    { label: "" }
];

// Alphabetical by name is the default — admins land on a predictable list and
// can re-sort via the column headers when triaging.
const sortKey = ref<SortKey>("name");
const sortDir = ref<SortDir>("asc");

// Heartbeat age in seconds; null heartbeat = oldest (Infinity).
const heartbeatAge = (t: IAdminTrust): number =>
    t.last_heartbeat ? (Date.now() - new Date(t.last_heartbeat).getTime()) / 1000 : Infinity;

const SORT_COMPARATORS: Record<SortKey, (a: IRenderedTrust, b: IRenderedTrust) => number> = {
    severity: (a, b) => STATE_RANK[a._state] - STATE_RANK[b._state] || a.name.localeCompare(b.name),
    name: (a, b) => a.name.localeCompare(b.name),
    region: (a, b) => (a.region ?? "").localeCompare(b.region ?? "") || a.name.localeCompare(b.name),
    heartbeat: (a, b) => heartbeatAge(a) - heartbeatAge(b) || a.name.localeCompare(b.name),
    projects: (a, b) => a.project_count - b.project_count || a.name.localeCompare(b.name)
};

// First click on a new column sorts ascending; subsequent clicks toggle direction.
const toggleSort = (key: SortKey) => {
    if (sortKey.value === key) {
        sortDir.value = sortDir.value === "asc" ? "desc" : "asc";
    }
    else {
        sortKey.value = key;
        sortDir.value = "asc";
    }
};

// Pre-compute per-row state + sparkline geometry once per data refresh so the
// template doesn't re-derive them on every paint (saves ~5 trustState() calls
// per row plus the nested uptimeValues → uptimeDots → uptimePoints chain).
const sortedTrusts = computed<IRenderedTrust[]>(() => {
    if (!trusts.value) return [];
    const arr: IRenderedTrust[] = trusts.value.map(t => {
        const state = trustState(t);
        const values = UPTIME_BY_STATE[state];
        const xStep = uptimeSvg.w / (values.length - 1);
        const dots = values.map((v, i) => ({
            x: i * xStep,
            y: uptimeSvg.h - v * uptimeSvg.h
        }));

        return {
            ...t,
            _state: state,
            _uptimePoints: dots.map(p => `${p.x},${p.y}`).join(" "),
            _uptimeDots: dots
        };
    });
    const cmp = SORT_COMPARATORS[sortKey.value];
    arr.sort(sortDir.value === "asc" ? cmp : (a, b) => cmp(b, a));

    return arr;
});

const summaryCells = computed(() => {
    const counts: Record<TrustState, number> = {
        online: 0,
        degraded: 0,
        offline: 0,
        disabled: 0
    };
    for (const t of trusts.value ?? []) counts[trustState(t)]++;

    return [
        {
            key: "online",
            label: "Online",
            count: counts.online,
            dotClass: "bg-emerald-600",
            warnTextClass: "text-emerald-700"
        },
        {
            key: "degraded",
            label: "Degraded",
            count: counts.degraded,
            dotClass: "bg-amber-500",
            warnTextClass: "text-amber-600 dark:text-amber-400"
        },
        {
            key: "offline",
            label: "Offline",
            count: counts.offline,
            dotClass: "bg-red-500",
            warnTextClass: "text-red-600 dark:text-red-400"
        },
        {
            key: "disabled",
            label: "Disabled",
            count: counts.disabled,
            dotClass: "bg-gray-400",
            warnTextClass: "text-gray-600 dark:text-gray-400"
        }
    ];
});

const subtitle = computed(() => {
    if (!trusts.value) return "Loading…";
    const incidents = trusts.value.filter(t => {
        const s = trustState(t);

        return s === "offline" || s === "degraded";
    }).length;
    if (incidents === 0) return "All trusts reporting healthy.";

    return `${incidents} ${incidents === 1 ? "incident needs" : "incidents need"} attention.`;
});

// SVG hex colors mirror DOT_CLASSES — Tailwind class names don't translate to
// `stroke=`/`fill=` attributes, so we duplicate the palette here for the radial.
const DOT_HEX: Record<TrustState, string> = {
    online: "#059669",
    degraded: "#f59e0b",
    offline: "#ef4444",
    disabled: "#9ca3af"
};

const radial = {
    cx: 380,
    cy: 380,
    R: 260
};

interface IRadialNode {
    id: string;
    name: string;
    code: string | null;
    region: string | null;
    last_heartbeat: string | null;
    project_count: number;
    state: TrustState;
    x: number;
    y: number;
    labelX: number;
    labelY: number;
    anchor: "start" | "end" | "middle";
    r: number;
    shortLabel: string;
}

// Pre-compute spoke endpoints + label positions so the template stays declarative.
// Node radius scales mildly with project count (capped at 6) to mirror the design.
const radialNodes = computed<IRadialNode[]>(() => {
    const arr = trusts.value ?? [];
    if (!arr.length) return [];
    const count = arr.length;

    return arr.map((t, i) => {
        const angle = (i / count) * Math.PI * 2 - Math.PI / 2;
        const x = radial.cx + Math.cos(angle) * radial.R;
        const y = radial.cy + Math.sin(angle) * radial.R;
        const labelX = radial.cx + Math.cos(angle) * (radial.R + 34);
        const labelY = radial.cy + Math.sin(angle) * (radial.R + 34);
        const anchor: "start" | "end" | "middle" =
            Math.cos(angle) > 0.1 ? "start" : Math.cos(angle) < -0.1 ? "end" : "middle";
        const r = 9 + Math.min(t.project_count, 6) * 1.2;

        return {
            id: t.id,
            name: t.name,
            code: t.code,
            region: t.region,
            last_heartbeat: t.last_heartbeat,
            project_count: t.project_count,
            state: trustState(t),
            x,
            y,
            labelX,
            labelY,
            anchor,
            r,
            shortLabel: t.code || t.name
        };
    });
});

const radialLegend = computed(() => [
    {
        state: "online" as TrustState,
        label: "Online",
        dotClass: "bg-emerald-600"
    },
    {
        state: "degraded" as TrustState,
        label: "Degraded",
        dotClass: "bg-amber-500"
    },
    {
        state: "offline" as TrustState,
        label: "Offline",
        dotClass: "bg-red-500"
    },
    {
        state: "disabled" as TrustState,
        label: "Disabled",
        dotClass: "bg-gray-400"
    }
]);

const onlineCount = computed(() => radialNodes.value.filter(n => n.state === "online").length);

const incidents = computed<IRenderedTrust[]>(() =>
    sortedTrusts.value.filter(t => t._state === "offline" || t._state === "degraded")
);

const hoveredTrust = computed<IRenderedTrust | null>(
    () => sortedTrusts.value.find(t => t.id === hoverTrustId.value) ?? null
);

const onTrustCreated = (newTrust: ICreatedTrust) => {
    showAddTrustModal.value = false;
    createdTrust.value = newTrust;
    refresh();
};

const onKitModalClose = () => {
    createdTrust.value = null;
};
</script>

<style scoped>
.connection-topology-card {
    background: radial-gradient(ellipse at center, #fff 0%, #f5f0f2 70%, #efe8ec 100%);
}

.dark .connection-topology-card {
    background: radial-gradient(ellipse at center, #1f2937 0%, #111827 68%, #0f172a 100%);
}
</style>
