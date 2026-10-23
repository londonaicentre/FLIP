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

// Lighthouse CI config for the FLIP UI, driven by the `lighthouse_flip_ui`
// workflow. It re-runs the audit behind issue #586 in a clean, repeatable
// environment (headless Chrome on a CI runner, no browser extensions) —
// which is exactly what the original prod capture lacked.
//
// Scope: `/auth/login`, then — when LHCI_USERNAME is set — the main signed-in
// pages. The workflow starts the AWS-free hub (flip-api + the dev Keycloak),
// seeds one project (LHCI_PROJECT_ID) and signs in as the seeded admin through
// lighthouse-auth.cjs. Keycloak and Cognito ship in the same bundle (the
// backend is picked at runtime from js/window.js), so this audits the JS prod
// serves. Without LHCI_USERNAME (a local run with no hub) only the login page
// is audited.
// `staticDistDir` is deliberately NOT used: it can't do SPA history fallback,
// so a deep link to /auth/login would 404. `vite preview` serves the built
// bundle *and* falls back to index.html, matching how CloudFront serves prod.
//
// NOTE: CSP and source-map findings (issues #703 / the deferred source-maps
// item on #586) depend on CloudFront response headers, which `vite preview`
// does not emit — those can only be validated against a real deployment, not
// here.
// The workflow's UI_PORT, which also sets the hub's redirect URI and CORS origin.
const UI_PORT = process.env.UI_PORT || "4173";
const BASE_URL = `http://localhost:${UI_PORT}`;
const SIGNED_IN = Boolean(process.env.LHCI_USERNAME);
const SIGNED_IN_PATHS = [
    "/projects",
    ...(process.env.LHCI_PROJECT_ID ? [`/project/${process.env.LHCI_PROJECT_ID}`] : []),
    "/models",
    "/admin/users",
];
// Each page twice, light then dark: lighthouse-auth.cjs sets the theme from
// the `#dark` fragment, which reaches neither the server nor the router. Both
// login passes come first, while the browser is still signed out.
const PATHS = ["/auth/login", ...(SIGNED_IN ? SIGNED_IN_PATHS : [])];
const URLS = SIGNED_IN ? PATHS.flatMap((path) => [path, `${path}#dark`]) : PATHS;
// Chrome on CI runners needs --no-sandbox; --disable-gpu avoids headless GPU
// flakiness. --disable-extensions keeps the profile clean (the whole point of
// re-running — the prod capture was polluted by ad-blocker extension scripts).
const CHROME_FLAGS = ["--no-sandbox", "--disable-gpu", "--disable-extensions"];
const ASSERTIONS = {
    "categories:accessibility": ["warn", { minScore: 0.95 }],
    "categories:best-practices": ["warn", { minScore: 0.9 }],
    "categories:performance": ["warn", { minScore: 0.9 }],
};

module.exports = {
    ci: {
        collect: {
            // Serve the already-built dist/ (the workflow runs `build:deploy`
            // first). `vite preview` gives SPA history fallback so the deep
            // link to /auth/login resolves to the app rather than a 404.
            startServerCommand: `npm run serve -- --port ${UI_PORT} --strictPort`,
            // Vite colors `Local` with ANSI escapes in CI, so match the text
            // across those codes as well as an optional space before the colon.
            startServerReadyPattern: "Local(?:\\x1b\\[[0-9;]*m)*\\s*:",
            startServerReadyTimeout: 30000,
            url: URLS.map((path) => BASE_URL + path),
            // With a puppeteerScript lhci launches Chrome through puppeteer and
            // ignores settings.chromeFlags, so the flags move to its launch args.
            ...(SIGNED_IN && {
                puppeteerScript: "./lighthouse-auth.cjs",
                puppeteerLaunchOptions: { args: CHROME_FLAGS },
            }),
            // Median of 3 runs — smooths the timing-based performance metrics;
            // the accessibility / SEO audits are deterministic regardless.
            numberOfRuns: 3,
            settings: {
                // Match the original audit (Lighthouse v13, desktop form factor).
                preset: "desktop",
                ...(!SIGNED_IN && { chromeFlags: CHROME_FLAGS.join(" ") }),
            },
        },
        assert: {
            // All `warn` for now: this job is informational until a green
            // baseline is observed on a few runs. Once the numbers are trusted,
            // promote accessibility + seo to `error` (they're audit-based and
            // deterministic) to turn the #586 target into an enforced gate.
            // Leave best-practices at `warn`: it's dinged by the known-deferred
            // console 401s (#702) and CSP (#703). Leave performance at `warn`:
            // it's timing-based and varies run-to-run on shared CI runners.
            // SEO is asserted on the login page only: robots.txt disallows the
            // signed-in pages, so they fail `is-crawlable` by design.
            assertMatrix: [
                {
                    matchingUrlPattern: "/auth/login(#dark)?$",
                    assertions: { ...ASSERTIONS, "categories:seo": ["warn", { minScore: 0.95 }] },
                },
                { matchingUrlPattern: "^(?!.*/auth/login(#dark)?$)", assertions: ASSERTIONS },
            ],
        },
        upload: {
            // Keep reports inside GitHub (uploaded as a build artifact by the
            // workflow) rather than pushing to a third-party public bucket.
            target: "filesystem",
            outputDir: "./.lighthouseci",
        },
    },
};
