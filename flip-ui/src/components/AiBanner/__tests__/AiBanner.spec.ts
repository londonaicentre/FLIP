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
import { describe, expect, it } from "vitest";

import AiBanner from "@/components/AiBanner/AiBanner.vue";

function mountBanner(link?: string) {
    return mount(AiBanner, {
        props: {
            message: "msg",
            link
        },
        global: {
            // The `Close for this session` icon uses v-tippy; stub the directive.
            directives: { tippy: {} }
        }
    });
}

describe("AiBanner link sanitisation", () => {
    it("renders the Learn more link for an https URL", () => {
        const anchor = mountBanner("https://example.com").find("[data-test='banner-link']");

        expect(anchor.exists()).toBe(true);
        expect(anchor.attributes("href")).toBe("https://example.com");
        // target=_blank without rel=noopener is a reverse-tabnabbing risk.
        expect(anchor.attributes("rel")).toContain("noopener");
    });

    it("renders the link for an http URL", () => {
        const anchor = mountBanner("http://example.com").find("[data-test='banner-link']");

        expect(anchor.exists()).toBe(true);
        expect(anchor.attributes("href")).toBe("http://example.com");
    });

    it.each([
        "javascript:alert(document.cookie)",
        "JavaScript:alert(1)",
        "  javascript:alert(1)  ",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "not-a-url"
    ])("does not render the link for non-http(s) scheme %s", (link) => {
        expect(mountBanner(link).find("[data-test='banner-link']").exists()).toBe(false);
    });

    it("renders no link when none is provided", () => {
        expect(mountBanner(undefined).find("[data-test='banner-link']").exists()).toBe(false);
    });
});
