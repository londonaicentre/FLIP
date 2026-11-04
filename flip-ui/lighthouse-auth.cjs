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

// lhci `puppeteerScript`: sets the theme for the URL about to be audited and
// signs the audit browser in through the real login form before each
// signed-in URL. lhci calls it once per URL, ahead of that URL's runs;
// Lighthouse's storage reset leaves localStorage alone, so both the theme and
// the Keycloak session it stores there carry across every run.
module.exports = async (browser, { url }) => {
    const { origin, pathname, hash } = new URL(url);
    const page = await browser.newPage();
    try {
        await page.goto(`${origin}/auth/login`, { waitUntil: "networkidle0" });
        // The key vueuse's useDark (App.vue) persists the theme under.
        await page.evaluate((scheme) => localStorage.setItem("vueuse-color-scheme", scheme),
            hash === "#dark" ? "dark" : "light");
        // The login page itself is audited signed out (its URLs come first).
        if (pathname === "/auth/login") {
            return;
        }

        const signedIn = await page.evaluate(() =>
            Object.keys(localStorage).some((key) => key.startsWith("flip.auth.keycloak.")));
        if (!signedIn) {
            await page.type("input[data-test=username]", process.env.LHCI_USERNAME);
            await page.type("input[data-test=password]", process.env.LHCI_PASSWORD);
            await Promise.all([
                page.waitForFunction(() => location.pathname === "/projects", { timeout: 30000 }),
                page.keyboard.press("Enter"),
            ]);
        }
    } finally {
        await page.close();
    }
};
