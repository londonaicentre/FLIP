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




import { mount } from "@vue/test-utils";

import AiPagination from "../AiPagination.vue";

describe("Ai Pagination", () => {
    test("Renders Component", () => {
        const comp = mount(AiPagination);

        expect(comp.element).toMatchSnapshot();
    });

    test("emits pageUpdate from the prev, next and numbered buttons", async () => {
        const comp = mount(AiPagination, {
            props: {
                totalPages: 5,
                currentPage: 3
            }
        });

        await comp.find("[data-test='page-btn-prev']").trigger("click");
        await comp.find("[data-test='page-btn-next']").trigger("click");
        await comp.find("[data-test='page-btn-5']").trigger("click");

        expect(comp.emitted("pageUpdate")).toEqual([[2], [4], [5]]);
    });

    test("jumps to the first and last pages from the ellipsis-edge buttons", async () => {
        // A mid-range page on a long list renders the dedicated 1 / totalPages
        // jump buttons outside the sliding window.
        const comp = mount(AiPagination, {
            props: {
                totalPages: 10,
                currentPage: 5
            }
        });

        await comp.find("[data-test='page-btn-1']").trigger("click");
        await comp.find("[data-test='page-btn-10']").trigger("click");

        expect(comp.emitted("pageUpdate")).toEqual([[1], [10]]);
    });

    test("renders the prev/next arrow icons", () => {
        const comp = mount(AiPagination, {
            props: {
                totalPages: 3,
                currentPage: 2
            }
        });

        expect(comp.find("[data-test='page-btn-prev']").find("svg").exists()).toBe(true);
        expect(comp.find("[data-test='page-btn-next']").find("svg").exists()).toBe(true);
    });

    test("disables prev on the first page and next on the last", () => {
        const first = mount(AiPagination, {
            props: {
                totalPages: 5,
                currentPage: 1
            }
        });
        expect(first.find("[data-test='page-btn-prev']").attributes("disabled")).toBeDefined();

        const last = mount(AiPagination, {
            props: {
                totalPages: 5,
                currentPage: 5
            }
        });
        expect(last.find("[data-test='page-btn-next']").attributes("disabled")).toBeDefined();
    });
});
