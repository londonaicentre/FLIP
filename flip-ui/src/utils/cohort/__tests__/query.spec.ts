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




import { filterByQueriedTrustIds } from "@/utils/cohort/query";

describe("Query", () => {
    describe("filterByQueriedTrustIds", () => {
        const trusts = [{ id: "a" }, { id: "b" }, { id: "c" }];

        it("returns input unchanged when ids are undefined (project still loading)", () => {
            expect(filterByQueriedTrustIds(trusts, undefined)).toEqual(trusts);
        });

        it("filters to the queried subset", () => {
            expect(filterByQueriedTrustIds(trusts, ["a", "c"])).toEqual([{ id: "a" }, { id: "c" }]);
        });

        it("filters everything out when the queried list is empty", () => {
            expect(filterByQueriedTrustIds(trusts, [])).toEqual([]);
        });
    });
});
