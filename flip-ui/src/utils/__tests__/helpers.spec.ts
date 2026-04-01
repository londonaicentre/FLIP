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

import { getRandomId } from "@/utils/helpers";

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
