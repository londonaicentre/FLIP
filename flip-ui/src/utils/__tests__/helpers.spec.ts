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

import { apiTimestampMs, getRandomId, relativeCreatedLabel } from "@/utils/helpers";

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

describe("getRandomId", () => {
    it("returns a valid UUID v4 string", () => {
        const id = getRandomId();
        expect(id).toMatch(UUID_REGEX);
    });

    it("returns a different value on each call", () => {
        const ids = new Set(Array.from({ length: 10 }, () => getRandomId()));
        expect(ids.size).toBe(10);
    });

    it("delegates to crypto.randomUUID", () => {
        const mockUUID = "00000000-0000-4000-8000-000000000000";
        const spy = vi.spyOn(crypto, "randomUUID").mockReturnValueOnce(mockUUID);
        expect(getRandomId()).toBe(mockUUID);
        spy.mockRestore();
    });
});

describe("apiTimestampMs", () => {
    it("parses an offset-less API timestamp as UTC, not browser-local time", () => {
        expect(apiTimestampMs("2026-06-01T12:00:00")).toBe(Date.UTC(2026, 5, 1, 12, 0, 0));
    });

    it("leaves an explicit offset alone", () => {
        expect(apiTimestampMs("2026-06-01T12:00:00Z")).toBe(Date.UTC(2026, 5, 1, 12, 0, 0));
        expect(apiTimestampMs("2026-06-01T13:00:00+01:00")).toBe(Date.UTC(2026, 5, 1, 12, 0, 0));
    });

    it("returns null for a missing or empty timestamp", () => {
        expect(apiTimestampMs(undefined)).toBeNull();
        expect(apiTimestampMs(null)).toBeNull();
        expect(apiTimestampMs("")).toBeNull();
    });

    it("returns null for an unparseable timestamp", () => {
        expect(apiTimestampMs("not-a-date")).toBeNull();
    });
});

describe("relativeCreatedLabel", () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date("2026-06-01T12:00:00Z"));
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    // Offset-less inputs throughout: the API serialises naive UTC, so these
    // prove the UTC treatment at every bucket boundary.
    it.each([
        ["2026-06-01T11:59:01", "created 59s ago"],
        ["2026-06-01T11:59:00", "created 1m ago"],
        ["2026-06-01T11:00:01", "created 59m ago"],
        ["2026-06-01T11:00:00", "created 1h ago"],
        ["2026-05-31T12:00:01", "created 23h ago"],
        ["2026-05-31T12:00:00", "created 1d ago"],
        ["2026-05-29T12:00:00", "created 3d ago"]
    ])("renders %s as '%s'", (timestamp, label) => {
        expect(relativeCreatedLabel(timestamp)).toBe(label);
    });

    it("clamps a future (clock-skewed) timestamp to 0s", () => {
        expect(relativeCreatedLabel("2026-06-01T12:30:00")).toBe("created 0s ago");
    });

    it("falls back to an em-dash for missing or unparseable timestamps", () => {
        expect(relativeCreatedLabel(undefined)).toBe("—");
        expect(relativeCreatedLabel(null)).toBe("—");
        expect(relativeCreatedLabel("not-a-date")).toBe("—");
    });
});
