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
import { VueWrapper } from "@vue/test-utils";

import PageNotFound from "@/pages/[...PageNotFound].vue";

const mockRouteChangeBack = vi.fn();

vi.mock("@/router", () => ({
    routeChange: { back: (...args: unknown[]) => mockRouteChangeBack(...args) },
    default: { push: vi.fn() }
}));

// The stub renders the custom scoped slot (with a no-op navigate) so the
// primary button's content — hidden behind router-link's v-slot — is exercised.
const mountPage = (): VueWrapper => mountComponent(PageNotFound, {
    global: {
        stubs: {
            "router-link": {
                props: ["to"],
                template: "<a :data-to=\"to\"><slot :navigate=\"() => {}\" /></a>"
            }
        }
    }
});

// vue-router records the previous in-app route at history.state.back; a direct deep link has none
const setHistoryBack = (back: string | null) => {
    window.history.replaceState({ back }, "");
};

describe("Page Not Found", () => {
    beforeEach(() => {
        mockRouteChangeBack.mockClear();
        setHistoryBack(null);
    });

    it("renders the badge, eyebrow, headline and body copy", () => {
        const component = mountPage();

        expect(component.find("[data-test='badge-icon']").exists()).toBe(true);
        expect(component.find("[data-test='eyebrow']").text()).toBe("Error 404");
        expect(component.find("h1").text()).toBe("We can't find the page you have requested.");
        expect(component.find("[data-test='subtitle']").text())
            .toBe("Check the address and try again. If the issue persists, please contact the service desk.");
    });

    it("links the primary action to the projects page", () => {
        const component = mountPage();

        const link = component.find("a[data-to='/projects']");
        expect(link.exists()).toBe(true);
        expect(link.text()).toContain("View Projects");
    });

    it("hides the Go back button when there is no history entry to return to", () => {
        const component = mountPage();

        expect(component.find("[data-test='back-button']").exists()).toBe(false);
    });

    it("navigates back through the router when Go back is clicked", async () => {
        setHistoryBack("/projects");
        const component = mountPage();

        await component.find("[data-test='back-button']").trigger("click");

        expect(mockRouteChangeBack).toHaveBeenCalledTimes(1);
    });

    it("offers a service desk mailto link", () => {
        const component = mountPage();

        const link = component.find("[data-test='service-desk-link']");
        expect(link.text()).toBe("Contact the service desk");
        expect(link.attributes("href")).toBe("mailto:aicentreflip@gmail.com");
    });

    it("renders the component successfully", () => {
        const component = mountPage();

        expect(component.element).toMatchSnapshot();
    });
});
