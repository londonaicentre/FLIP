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

import { createApiDrafter, type IHubDraftResponse, toResult } from "../api-drafter";

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/services/api", () => ({
    _http: {
        get: (...args: unknown[]) => mockGet(...args),
        post: (...args: unknown[]) => mockPost(...args)
    }
}));

const hubUsage = {
    enabled: true,
    used: 4,
    limit: 30,
    remaining: 26,
    resets_at: "2026-12-01T00:00:00Z"
};

const response = (over: Partial<IHubDraftResponse>): IHubDraftResponse => ({
    status: "error",
    sql: null,
    explanation: null,
    assumptions: [],
    concepts_used: [],
    question: null,
    options: [],
    error_code: null,
    message: null,
    usage: hubUsage,
    ...over
});

describe("api drafter", () => {
    beforeEach(() => {
        mockGet.mockReset();
        mockPost.mockReset();
    });

    it("maps the usage route", async () => {
        mockGet.mockResolvedValue({ data: hubUsage });

        expect(await createApiDrafter("p1").getUsage()).toEqual({
            enabled: true,
            used: 4,
            limit: 30,
            resetsAt: "2026-12-01T00:00:00Z"
        });
        expect(mockGet).toHaveBeenCalledWith("/cohort/draft/usage");
    });

    it("posts the project, description and answers with a fresh idempotency key, never hasImaging", async () => {
        mockPost.mockResolvedValue({
            data: response({
                status: "draft",
                sql: "SELECT 1"
            })
        });
        const signal = new AbortController().signal;
        const drafter = createApiDrafter("p1");
        const answers = [{
            question: "Which modality?",
            answer: "CT"
        }];

        await drafter.draft({
            description: "CT scans",
            hasImaging: true,
            clarifications: answers
        }, signal);
        await drafter.draft({
            description: "CT scans",
            hasImaging: true
        }, signal);

        const [url, body, config] = mockPost.mock.calls[0];
        expect(url).toBe("/cohort/draft");
        expect(body).toEqual({
            project_id: "p1",
            description: "CT scans",
            clarifications: answers
        });
        expect(config.signal).toBe(signal);
        expect(config.timeout).toBeGreaterThan(30_000);
        expect(config.headers["Idempotency-Key"]).toBeTruthy();
        expect(mockPost.mock.calls[1][2].headers["Idempotency-Key"]).not.toBe(config.headers["Idempotency-Key"]);
        expect(mockPost.mock.calls[1][1].clarifications).toEqual([]);
    });

    it("maps each outcome to the assistant's states", () => {
        expect(toResult(response({
            status: "draft",
            sql: "SELECT 1",
            explanation: "e",
            assumptions: ["a"],
            concepts_used: ["8532 = female"]
        }), "d")).toEqual({
            status: "draft",
            description: "d",
            sql: "SELECT 1",
            explanation: "e",
            assumptions: ["a"],
            conceptsUsed: ["8532 = female"]
        });
        expect(toResult(response({
            status: "clarification",
            question: "CT or MRI?",
            options: ["CT", "MRI"]
        }), "d")).toEqual({
            status: "needs_clarification",
            question: "CT or MRI?",
            options: ["CT", "MRI"]
        });
        expect(toResult(response({
            status: "decline",
            message: "Read-only cohorts only."
        }), "d")).toEqual({
            status: "declined",
            explanation: "Read-only cohorts only."
        });
        expect(toResult(response({ error_code: "allowance_exhausted" }), "d")).toEqual({ status: "quota_exhausted" });
    });

    it("throws the hub's message for any other error, so the editor is left alone", () => {
        for (const code of ["request_cap_reached", "rate_limited", "model_unavailable", "invalid_draft"]) {
            expect(() => toResult(response({
                error_code: code,
                message: `msg ${code}`
            }), "d")).toThrow(`msg ${code}`);
        }
    });
});
