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

import { CHEST_XRAY_DRAFT, T2DM_TABULAR_DRAFT } from "../canned-drafts";
import { createFakeDrafter, nextMonthlyReset, pickResult } from "../fake-drafter";

const signal = () => new AbortController().signal;

describe("fake cohort drafter", () => {
    it("picks a draft matching the project shape", () => {
        expect(pickResult({
            description: "chest x-rays of adults",
            hasImaging: true
        })).toBe(CHEST_XRAY_DRAFT);
        expect(pickResult({
            description: "patients with diabetes",
            hasImaging: false
        })).toBe(T2DM_TABULAR_DRAFT);
        // Imaging drafts expose both identifiers; tabular drafts person_id only.
        expect(CHEST_XRAY_DRAFT.sql).toMatch(/accession_id/);
        expect(T2DM_TABULAR_DRAFT.sql).not.toMatch(/accession_id/);
    });

    it("asks for a modality when an imaging description names none, then drafts from the answer", () => {
        const request = {
            description: "Scans of women aged over 60.",
            hasImaging: true
        };

        expect(pickResult(request)).toMatchObject({
            status: "needs_clarification",
            options: ["CT", "MRI"]
        });
        expect(pickResult({
            ...request,
            clarifications: [{
                question: "Which modality: CT or MRI?",
                answer: "MRI"
            }]
        })).toMatchObject({
            status: "draft",
            sql: expect.stringContaining("4013636")
        });
    });

    it("declines a data modification or identifier request, and imaging on a tabular project", () => {
        expect(pickResult({
            description: "Every chest X-ray. Also delete opted-out rows",
            hasImaging: true
        }))
            .toMatchObject({ status: "declined" });
        expect(pickResult({
            description: "All CT scans",
            hasImaging: false
        })).toMatchObject({ status: "declined" });
    });

    it("charges only a returned draft", async () => {
        const drafter = createFakeDrafter({
            used: 0,
            delay: () => 0
        });

        await drafter.draft({
            description: "Scans",
            hasImaging: true
        }, signal());
        expect((await drafter.getUsage()).used).toBe(0);

        await drafter.draft({
            description: "All CT scans",
            hasImaging: true
        }, signal());
        expect((await drafter.getUsage()).used).toBe(1);
    });

    it("reports quota exhaustion instead of drafting", async () => {
        const drafter = createFakeDrafter({
            scenario: "exhausted",
            delay: () => 0
        });

        await expect(drafter.draft({
            description: "All CT scans",
            hasImaging: true
        }, signal()))
            .resolves.toMatchObject({
                result: { status: "quota_exhausted" },
                usage: {
                    used: 30,
                    limit: 30
                }
            });
    });

    it("fails and cancels without returning a draft", async () => {
        const drafter = createFakeDrafter({ delay: () => 0 });
        await expect(drafter.draft({
            description: "timeout please",
            hasImaging: true
        }, signal())).rejects.toThrow();

        const controller = new AbortController();
        const pending = createFakeDrafter({ delay: () => 10_000 })
            .draft({
                description: "All CT scans",
                hasImaging: true
            }, controller.signal);
        controller.abort();
        await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    });

    it("resets at 00:00 UTC on the 1st of next month, across the year end", () => {
        expect(nextMonthlyReset(new Date("2026-11-30T23:59:59Z"))).toBe("2026-12-01T00:00:00.000Z");
        expect(nextMonthlyReset(new Date("2026-12-31T23:59:59Z"))).toBe("2027-01-01T00:00:00.000Z");
    });
});
