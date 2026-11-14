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
import { beforeEach, describe, expect, it, vi } from "vitest";

import { IHealthResponse } from "@/services/health-service";
import { useHealthcheckStore } from "@/store/healthcheck";

describe("healthcheckStore", () => {
    beforeEach(() => {
        setActivePinia(createPinia());
    });

    it("starts with no trust health", () => {
        expect(useHealthcheckStore().getHealth.trustHealth).toEqual([]);
    });

    it("stores the latest trust health and stamps when it arrived", async () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
        const store = useHealthcheckStore();
        const health = [{ trustName: "Trust_1" }] as unknown as IHealthResponse[];

        vi.setSystemTime(new Date("2026-01-01T00:05:00Z"));
        await store.setHealth(health);

        expect(store.getHealth.trustHealth).toEqual(health);
        expect(store.lastUpdated).toBe(new Date("2026-01-01T00:05:00Z").getTime());
        vi.useRealTimers();
    });
});
