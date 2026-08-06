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




import { mountComponent } from "@test/helper";

import AiBanner from "../AiBanner.vue";


describe("AiBanner", () => {
    test("Renders Component", () => {
        const comp = mountComponent(AiBanner, { props: { message: "Some cool message" } });

        expect(comp.element).toMatchSnapshot();
    });

    test("renders the link when the scheme is http(s)", () => {
        const comp = mountComponent(AiBanner, {
            props: {
                message: "Some cool message",
                link: "https://example.nhs.uk"
            }
        });

        expect(comp.find("[data-test=\"banner-link\"]").attributes("href")).toBe("https://example.nhs.uk");
    });

    test("drops the link entirely when the scheme is not http(s)", () => {
        // A javascript: href would execute in every user's session. The affordance should
        // disappear rather than render as an anchor that silently does nothing.
        const comp = mountComponent(AiBanner, {
            props: {
                message: "Some cool message",
                link: "javascript:alert(1)"
            }
        });

        expect(comp.find("[data-test=\"banner-link\"]").exists()).toBe(false);
    });
});
