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

import { describe, expect, it } from "vitest";

import { EARLIEST_CAPTURE_DATE, LATEST_CAPTURE_DATE, PROJECT_COUNT } from "../demo/capture-window";
import { captureWindow, DEMO_PROJECTS } from "../demo/registry";
import { DEMO_SITES } from "../demo/roster";

type Models = { data: { id: string }[] };


describe("demo project registry", () => {
    it("registers the five example projects, Ark+ first", () => {
        expect(DEMO_PROJECTS.map(p => p.slug)).toEqual([
            "arkplus-finetune",
            "arkplus-evaluation",
            "ehr-risk",
            "spleen-segmentation",
            "spleen-evaluation"
        ]);
    });

    it("lists the Ark+ projects first, in order", () => {
        expect(DEMO_PROJECTS.slice(0, 2).map(p => p.slug)).toEqual(["arkplus-finetune", "arkplus-evaluation"]);
        expect(new Set(DEMO_PROJECTS.map(p => p.slug)).size).toBe(DEMO_PROJECTS.length);
    });

    it("approves only roster sites, named as the roster names them", () => {
        for (const demo of DEMO_PROJECTS) {
            const project = demo.project as { approvedTrusts: { id: string; name: string; code: string }[] };
            for (const trust of project.approvedTrusts) {
                const site = DEMO_SITES.find(s => s.id === trust.id);
                expect(site, `${demo.slug}: ${trust.id} is not a roster site`).toBeDefined();
                expect([trust.name, trust.code], demo.slug).toEqual([site?.name, site?.code]);
            }
        }
    });

    it("keys every model fixture by a model id present in that project's models list", () => {
        for (const raw of DEMO_PROJECTS) {
            const modelIds = (raw.models as Models).data.map(model => model.id);
            expect(Object.keys(raw.modelFixtures).sort()).toEqual([...modelIds].sort());
        }
    });

    it("dates every capture", () => {
        for (const demo of DEMO_PROJECTS) {
            expect(demo.captureDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
        }
    });

    it("pins capture-window.ts to what the registry computes", () => {
        expect(captureWindow()).toEqual({
            projectCount: PROJECT_COUNT,
            earliest: EARLIEST_CAPTURE_DATE,
            latest: LATEST_CAPTURE_DATE
        });
    });
});
