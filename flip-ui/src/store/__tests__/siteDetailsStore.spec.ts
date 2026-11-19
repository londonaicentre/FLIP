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

import { createPinia, setActivePinia } from "pinia";

import { updateSiteDetails } from "@/services/site-service";
import { ISiteBanner, ISiteDetails, useSiteDetailsStore } from "@/store/siteDetailsStore";
import { Snackbar } from "@/utils/snackbar";

vi.mock("@/services/site-service", () => ({ updateSiteDetails: vi.fn() }));

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        success: vi.fn(),
        error: vi.fn()
    }
}));

const mockBanner: ISiteBanner = {
    message: "Test banner message",
    link: "https://example.com",
    enabled: true
};

const mockResponse: ISiteDetails = {
    banner: mockBanner,
    deploymentMode: true
};

describe("siteDetailsStore", () => {
    let store: ReturnType<typeof useSiteDetailsStore>;

    beforeEach(() => {
        setActivePinia(createPinia());
        store = useSiteDetailsStore();
        vi.mocked(updateSiteDetails).mockReset();
        vi.mocked(Snackbar.success).mockReset();
        vi.mocked(Snackbar.error).mockReset();
    });

    describe("initial state", () => {
        it("has undefined banner and deploymentMode false by default", () => {
            expect(store.banner).toBeUndefined();
            expect(store.deploymentMode).toBe(false);
        });
    });

    describe("getSiteDetails", () => {
        it("returns the current state", () => {
            const details = store.getSiteDetails;

            expect(details.banner).toBeUndefined();
            expect(details.deploymentMode).toBe(false);
        });
    });

    describe("setSiteDetails", () => {
        it("sets state with full details", () => {
            store.setSiteDetails({
                banner: mockBanner,
                deploymentMode: true
            });

            expect(store.banner).toEqual(mockBanner);
            expect(store.deploymentMode).toBe(true);
        });

        it("sets state without banner", () => {
            store.setSiteDetails({ deploymentMode: true });

            expect(store.banner).toBeUndefined();
            expect(store.deploymentMode).toBe(true);
        });
    });

    describe("updateBanner", () => {
        it.each([true, false])("sends the current deploymentMode (%s) with the banner and stores the response", async (mode) => {
            store.setSiteDetails({ deploymentMode: mode });
            vi.mocked(updateSiteDetails).mockResolvedValue({
                banner: mockBanner,
                deploymentMode: mode
            });

            await store.updateBanner(mockBanner);

            expect(updateSiteDetails).toHaveBeenCalledWith("/site/details", {
                banner: mockBanner,
                deploymentMode: mode
            });
            expect(store.banner).toEqual(mockBanner);
            expect(store.deploymentMode).toBe(mode);
        });

        it("shows success snackbar on success", async () => {
            vi.mocked(updateSiteDetails).mockResolvedValue(mockResponse);

            await store.updateBanner(mockBanner);

            expect(Snackbar.success).toHaveBeenCalledWith({
                title: "Banner Updated",
                text: "The banner has been updated."
            });
        });

        it("shows error snackbar and does not update state on failure", async () => {
            vi.mocked(updateSiteDetails).mockRejectedValue(new Error("API error"));

            await store.updateBanner(mockBanner);

            expect(Snackbar.error).toHaveBeenCalledWith({
                title: "Banner Not Updated",
                text: "The banner has not been updated."
            });
            expect(store.banner).toBeUndefined();
            expect(store.deploymentMode).toBe(false);
        });
    });

    describe("updateDeploymentMode", () => {
        it("calls updateSiteDetails with correct payload and updates state on success", async () => {
            store.setSiteDetails({
                banner: mockBanner,
                deploymentMode: false
            });
            vi.mocked(updateSiteDetails).mockResolvedValue(mockResponse);

            await store.updateDeploymentMode(true);

            expect(updateSiteDetails).toHaveBeenCalledWith("/site/details", {
                banner: mockBanner,
                deploymentMode: true
            });
            expect(store.deploymentMode).toBe(true);
        });

        it("shows success snackbar on success", async () => {
            vi.mocked(updateSiteDetails).mockResolvedValue(mockResponse);

            await store.updateDeploymentMode(true);

            expect(Snackbar.success).toHaveBeenCalledWith({
                title: "Deployment Mode Updated",
                text: "The deployment mode has been updated."
            });
        });

        // NOTE: The error message says "Banner Not Updated" — likely a copy-paste bug in the source code.
        // Asserting actual behavior here.
        it("shows error snackbar and does not update state on failure", async () => {
            const initialBanner: ISiteBanner = {
                message: "Initial",
                link: "",
                enabled: false
            };
            store.setSiteDetails({
                banner: initialBanner,
                deploymentMode: false
            });
            vi.mocked(updateSiteDetails).mockRejectedValue(new Error("API error"));

            await store.updateDeploymentMode(true);

            expect(Snackbar.error).toHaveBeenCalledWith({
                title: "Banner Not Updated",
                text: "The banner has not been updated."
            });
            expect(store.deploymentMode).toBe(false);
            expect(store.banner).toEqual(initialBanner);
        });
    });
});
