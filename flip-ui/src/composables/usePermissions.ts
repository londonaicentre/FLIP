/*
 * Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *     http://www.apache.org/licenses/LICENSE-2.0
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { computed, ComputedRef } from "vue";

import { useAuthStore } from "@/store/auth";

/**
 * Central role-based gates. Use these instead of inlining `hasPermissions(...)`
 * calls so changes to the permission model (e.g. role → permission mapping)
 * only need editing in one place.
 *
 * The Admin / Researcher / Viewer split:
 *   - Admin       — has `CanAccessAdminPanel` (every permission, by seed).
 *   - Researcher  — has `CanCreateProjects` (no `CanManageProjects`; per-project
 *                   access is enforced server-side on writes).
 *   - Viewer      — has neither.
 *   - Trust Admin — a Researcher who can also read projects staged at their trust (FLIP#1258); on those they are
 *                   not on, they are read-only (see `readsOnlyAsTrustAdmin`).
 */
export function usePermissions(): {
    isAdmin: ComputedRef<boolean>;
    canCreateProjects: ComputedRef<boolean>;
    /**
     * True when the user has no project-write capability. Use this as the
     * gate for any "create / edit / run / delete" UI control on a project,
     * model, or cohort query. Researchers always return `false` here —
     * per-project write authority is enforced server-side based on
     * project ownership / membership.
     */
    isViewer: ComputedRef<boolean>;
    /**
     * True when the user sees this project only as a Trust Admin of a trust it is staged at — they neither own
     * it nor are a member, and cannot manage every project. The API refuses them every write there.
     */
    readsOnlyAsTrustAdmin: (project?: { ownerId?: string; users?: { id: string }[] } | null) => boolean;
} {
    const authStore = useAuthStore();
    const isAdmin = computed(() => authStore.hasPermissions(["CanAccessAdminPanel"]));
    const canCreateProjects = computed(() => authStore.hasPermissions(["CanCreateProjects"]));
    const isViewer = computed(() => !canCreateProjects.value);

    const readsOnlyAsTrustAdmin = (project?: { ownerId?: string; users?: { id: string }[] } | null): boolean => {
        if (!authStore.trustAdminOf || !project || authStore.hasPermissions(["CanManageProjects"])) return false;
        const me = authStore.user?.userId;

        return project.ownerId !== me && !(project.users ?? []).some(u => u.id === me);
    };

    return {
        isAdmin,
        canCreateProjects,
        isViewer,
        readsOnlyAsTrustAdmin
    };
}
