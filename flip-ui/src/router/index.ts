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




import { setupLayouts } from "virtual:generated-layouts";
import generatedRoutes from "virtual:generated-pages";
import { createRouter, createWebHistory } from "vue-router";

const routes = setupLayouts(generatedRoutes);

// import { routes } from "@/router/routes";
import { doneRouteProgress, startRouteProgress } from "@/router/progress";
import { authCheck } from "@/utils/auth";

// Guards a one-time reload when a lazy route chunk fails to load (see onError).
const CHUNK_RELOAD_KEY = "flip:chunk-reload";

const router = createRouter({
    history: createWebHistory(),
    routes,
    scrollBehavior(to) {
        if (to.hash) {
            console.log(to);

            return document.querySelector(to.hash)?.scrollIntoView({ behavior: "smooth" });
        }
    }
});

router.beforeEach((to, from, next) => {
    startRouteProgress();
    /** Ensure the user is logged in */
    authCheck(to, from, next);
});

router.afterEach((_to, _from, failure) => {
    doneRouteProgress();
    // A clean navigation means the current chunks resolve, so re-arm the
    // stale-chunk reload guard for any future deploy.
    if (!failure) {
        sessionStorage.removeItem(CHUNK_RELOAD_KEY);
    }
});

// When a lazy route chunk fails to load — almost always because a new deploy
// has replaced the hashed chunk files this still-open tab references — recover
// by doing a single full reload to fetch the fresh index.html (and its current
// chunk manifest). The sessionStorage flag stops this looping if the failure
// is something other than a stale deploy.
router.onError((error, to) => {
    doneRouteProgress();
    const message = error instanceof Error ? error.message : String(error);
    const isChunkLoadError = /failed to fetch dynamically imported module/i.test(message)
        || /error loading dynamically imported module/i.test(message)
        || /importing a module script failed/i.test(message);
    if (isChunkLoadError && !sessionStorage.getItem(CHUNK_RELOAD_KEY)) {
        sessionStorage.setItem(CHUNK_RELOAD_KEY, "1");
        window.location.assign(to.fullPath);
    }
});


export const routeChange = {
    /**
     * Change route to login page
     */
    gotoLogin: (): void => {
        router.push({ path: "/auth/login" });
    },
    /**
     * Change route to view projects
     */
    viewProjects: (): void => {
        router.push({ path: "/projects" });
    },
    /**
     * Change route to view a project
     * @param projectId The project Id
     */
    viewProject: (projectId: string): void => {
        router.push({ path: `/project/${projectId}` });
    },
    /**
     * Change route to view models
     * @param projectId The project Id
     */
    viewModels: (projectId: string): void => {
        router.push({ path: `/project/${projectId}/models` });
    },
    /**
     * Change route to view a model
     * @param projectId The project Id
     * @param modelId The model Id
     */
    viewModel: (projectId: string, modelId: string): void => {
        router.push({ path: `/project/${projectId}/model/${modelId}` });
    },
    /**
     * Change route to add a cohort query
     */
    addCohortQuery: (projectId: string): void => {
        router.push({ path: `/project/${projectId}/cohort-query/create` });
    },
    /**
     * Change route to edit a cohort query
     */
    editCohortQuery: (projectId: string): void => {
        router.push({ path: `/project/${projectId}/cohort-query/edit` });
    },
    /**
     * Change route to not allowed
     */
    notAllowed: (): void => {
        router.push({ path: "/403" });
    },
    changePassword: (email: string): void => {
        router.push({
            name: "ChangePassword",
            params: { email }
        });
    },
    newPassword: (): void => {
        router.push({ path: "/auth/new-password" });
    },
    mfaSetup: (): void => {
        router.push({ path: "/auth/mfa-setup" });
    },
    mfaVerify: (): void => {
        router.push({ path: "/auth/mfa-verify" });
    },
    accessRequest: (): void => {
        router.push({ path: "/auth/access-request" });
    },
    back: (): void => {
        router.back();
    }
};

export default router;
