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
// Scope: the pre-auth `/auth/login` route only. That page talks to AWS
// Cognito (Amplify), never to flip-api, so no backend / trust / FL stack is
// needed — a production `vite build` served by `vite preview` is enough.
// `staticDistDir` is deliberately NOT used: it can't do SPA history fallback,
// so a deep link to /auth/login would 404. `vite preview` serves the built
// bundle *and* falls back to index.html, matching how CloudFront serves prod.
//
// NOTE: CSP and source-map findings (issues #703 / the deferred source-maps
// item on #586) depend on CloudFront response headers, which `vite preview`
// does not emit — those can only be validated against a real deployment, not
// here.
module.exports = {
    ci: {
        collect: {
            // Serve the already-built dist/ (the workflow runs `build:deploy`
            // first). `vite preview` gives SPA history fallback so the deep
            // link to /auth/login resolves to the app rather than a 404.
            startServerCommand: "npm run serve -- --port 4173 --strictPort",
            startServerReadyPattern: "Local:",
            startServerReadyTimeout: 30000,
            url: ["http://localhost:4173/auth/login"],
            // Median of 3 runs — smooths the timing-based performance metrics;
            // the accessibility / SEO audits are deterministic regardless.
            numberOfRuns: 3,
            settings: {
                // Match the original audit (Lighthouse v13, desktop form factor).
                preset: "desktop",
                // Chrome on CI runners needs --no-sandbox; --disable-gpu avoids
                // headless GPU flakiness. --disable-extensions keeps the profile
                // clean (the whole point of re-running — the prod capture was
                // polluted by ad-blocker extension scripts).
                chromeFlags: "--no-sandbox --disable-gpu --disable-extensions",
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
            assertions: {
                "categories:accessibility": ["warn", { minScore: 0.95 }],
                "categories:seo": ["warn", { minScore: 0.95 }],
                "categories:best-practices": ["warn", { minScore: 0.9 }],
                "categories:performance": ["warn", { minScore: 0.9 }],
            },
        },
        upload: {
            // Keep reports inside GitHub (uploaded as a build artifact by the
            // workflow) rather than pushing to a third-party public bucket.
            target: "filesystem",
            outputDir: "./.lighthouseci",
        },
    },
};
