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

import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("demo bootstrap", () => {
    beforeEach(() => {
        vi.resetModules();
        setActivePinia(createPinia());
    });

    afterEach(() => {
        vi.unstubAllEnvs();
    });

    it("seeds no identity outside the demo build", async () => {
        const { IS_DEMO, seedDemoAuth } = await import("@/demo/bootstrap");
        const { useAuthStore } = await import("@/store/auth");

        await seedDemoAuth();

        expect(IS_DEMO).toBe(false);
        expect(useAuthStore().user).toBeFalsy();
    });

    it("seeds a signed-in viewer with no permissions in the demo build", async () => {
        vi.stubEnv("VITE_DEMO", "true");
        const { IS_DEMO, seedDemoAuth } = await import("@/demo/bootstrap");
        const { useAuthStore } = await import("@/store/auth");

        await seedDemoAuth();

        expect(IS_DEMO).toBe(true);
        expect(useAuthStore().user).toMatchObject({
            username: "flip-demo",
            userId: "demo-researcher",
            permissions: []
        });
        expect(useAuthStore().hasPermissions(["CanCreateProjects"])).toBe(false);
    });
});
