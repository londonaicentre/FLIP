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

// Vite plugins (`vite-plugin-pages`, `vite-plugin-vue-layouts-next`) expose
// route config via virtual modules at build time; vitest can't resolve them,
// so stub them before the router module is loaded. Also short-circuit
// authCheck so the global beforeEach guard is a no-op during tests.
vi.mock("virtual:generated-pages", () => ({ default: [] }));
vi.mock("virtual:generated-layouts", () => ({ setupLayouts: (r: unknown) => r }));
vi.mock("@/utils/auth", () => ({ authCheck: vi.fn((_to, _from, next) => next?.()) }));
vi.mock("@/router/progress", () => ({
    startRouteProgress: vi.fn(),
    doneRouteProgress: vi.fn()
}));

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RouteLocationNormalized } from "vue-router";

import router, { afterEachGuard, beforeEachGuard, handleRouteError, routeChange } from "@/router";
import { doneRouteProgress, startRouteProgress } from "@/router/progress";
import { authCheck } from "@/utils/auth";

describe("routeChange", () => {
    let pushSpy: ReturnType<typeof vi.spyOn>;
    let backSpy: ReturnType<typeof vi.spyOn>;

    beforeEach(() => {
        pushSpy = vi.spyOn(router, "push").mockResolvedValue();
        backSpy = vi.spyOn(router, "back").mockImplementation(() => undefined);
    });

    it("gotoLogin pushes /auth/login", () => {
        routeChange.gotoLogin();
        expect(pushSpy).toHaveBeenCalledWith({ path: "/auth/login" });
    });

    it("viewProjects pushes /projects", () => {
        routeChange.viewProjects();
        expect(pushSpy).toHaveBeenCalledWith({ path: "/projects" });
    });

    it("viewProject pushes /project/:id", () => {
        routeChange.viewProject("proj-1");
        expect(pushSpy).toHaveBeenCalledWith({ path: "/project/proj-1" });
    });

    it("viewModels pushes /project/:id/models", () => {
        routeChange.viewModels("proj-2");
        expect(pushSpy).toHaveBeenCalledWith({ path: "/project/proj-2/models" });
    });

    it("viewModel pushes /project/:pid/model/:mid", () => {
        routeChange.viewModel("proj-3", "mdl-7");
        expect(pushSpy).toHaveBeenCalledWith({ path: "/project/proj-3/model/mdl-7" });
    });

    it("addCohortQuery pushes /project/:id/cohort-query/create", () => {
        routeChange.addCohortQuery("proj-4");
        expect(pushSpy).toHaveBeenCalledWith({ path: "/project/proj-4/cohort-query/create" });
    });

    it("editCohortQuery pushes /project/:id/cohort-query/edit", () => {
        routeChange.editCohortQuery("proj-5");
        expect(pushSpy).toHaveBeenCalledWith({ path: "/project/proj-5/cohort-query/edit" });
    });

    it("notAllowed pushes /403", () => {
        routeChange.notAllowed();
        expect(pushSpy).toHaveBeenCalledWith({ path: "/403" });
    });

    it("changePassword pushes /auth/change-password with email param", () => {
        routeChange.changePassword("a@b.com");
        // Name-based: route params only resolve against a named route.
        expect(pushSpy).toHaveBeenCalledWith({
            name: "ChangePassword",
            params: { email: "a@b.com" }
        });
    });

    it("newPassword pushes /auth/new-password", () => {
        routeChange.newPassword();
        expect(pushSpy).toHaveBeenCalledWith({ path: "/auth/new-password" });
    });

    it("mfaSetup pushes /auth/mfa-setup", () => {
        routeChange.mfaSetup();
        expect(pushSpy).toHaveBeenCalledWith({ path: "/auth/mfa-setup" });
    });

    it("mfaVerify pushes /auth/mfa-verify", () => {
        routeChange.mfaVerify();
        expect(pushSpy).toHaveBeenCalledWith({ path: "/auth/mfa-verify" });
    });

    it("accessRequest pushes /auth/access-request", () => {
        routeChange.accessRequest();
        expect(pushSpy).toHaveBeenCalledWith({ path: "/auth/access-request" });
    });

    it("back calls router.back()", () => {
        routeChange.back();
        expect(backSpy).toHaveBeenCalled();
    });
});

describe("navigation guards", () => {
    // Minimal RouteLocationNormalized stand-in — the guards only read `fullPath`.
    const to = { fullPath: "/projects" } as RouteLocationNormalized;
    const from = { fullPath: "/" } as RouteLocationNormalized;
    const assignMock = vi.fn();

    beforeEach(() => {
        vi.clearAllMocks();
        sessionStorage.clear();
        // jsdom's window.location.assign is a non-implemented no-op that warns;
        // stub it so the reload path is observable (and silent).
        vi.stubGlobal("location", { assign: assignMock });
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it("beforeEachGuard starts the progress bar and runs the auth check", () => {
        const next = vi.fn();
        beforeEachGuard(to, from, next);

        expect(startRouteProgress).toHaveBeenCalledOnce();
        expect(authCheck).toHaveBeenCalledWith(to, from, next);
    });

    it("afterEachGuard stops the progress bar and re-arms the reload guard on a clean navigation", () => {
        sessionStorage.setItem("flip:chunk-reload", "1");
        afterEachGuard(to, from, undefined);

        expect(doneRouteProgress).toHaveBeenCalledOnce();
        expect(sessionStorage.getItem("flip:chunk-reload")).toBeNull();
    });

    it("afterEachGuard leaves the reload guard set when the navigation failed", () => {
        sessionStorage.setItem("flip:chunk-reload", "1");
        // A NavigationFailure is truthy; the guard must not clear the flag.
        afterEachGuard(to, from, { type: 4 } as never);

        expect(doneRouteProgress).toHaveBeenCalledOnce();
        expect(sessionStorage.getItem("flip:chunk-reload")).toBe("1");
    });

    it("handleRouteError reloads once on a stale-chunk load error", () => {
        handleRouteError(new Error("Failed to fetch dynamically imported module: /assets/x.js"), to);

        expect(doneRouteProgress).toHaveBeenCalledOnce();
        expect(sessionStorage.getItem("flip:chunk-reload")).toBe("1");
        expect(assignMock).toHaveBeenCalledWith("/projects");
    });

    it("handleRouteError does not reload a second time once the guard flag is set", () => {
        sessionStorage.setItem("flip:chunk-reload", "1");
        handleRouteError(new Error("error loading dynamically imported module"), to);

        expect(assignMock).not.toHaveBeenCalled();
    });

    it("handleRouteError ignores non-chunk errors", () => {
        handleRouteError(new Error("some unrelated navigation error"), to);

        expect(assignMock).not.toHaveBeenCalled();
        expect(sessionStorage.getItem("flip:chunk-reload")).toBeNull();
    });

    it("handleRouteError coerces a non-Error rejection to a string before matching", () => {
        handleRouteError("importing a module script failed", to);

        expect(assignMock).toHaveBeenCalledWith("/projects");
    });
});
