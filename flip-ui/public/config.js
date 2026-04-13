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

/*
 * Runtime configuration for FLIP UI.
 *
 * DEVELOPMENT (Vite dev server / Docker/EC2)
 * ------------------------------------------
 * Leave this file empty (no window.__FLIP_CONFIG__ assignment).
 * The application reads configuration from import.meta.env, which Vite
 * populates from your .env.development file at startup.
 *
 * STATIC HOSTING (S3 + CloudFront)
 * ---------------------------------
 * The deployment pipeline should generate this file per environment and
 * upload it to S3 alongside the application bundle. Setting
 * window.__FLIP_CONFIG__ here takes precedence over any values baked into
 * the bundle at build time, allowing a single build artifact to be promoted
 * across dev / staging / production without a rebuild.
 *
 * Example generated config.js for a production environment:
 *
 *   window.__FLIP_CONFIG__ = {
 *     VITE_AWS_BASE_URL:             "https://api.example.com/api",
 *     VITE_AWS_USER_POOL_ID:         "eu-west-2_XXXXXXXXX",
 *     VITE_AWS_CLIENT_ID:            "xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
 *     VITE_AWS_REGION:               "eu-west-2",
 *     VITE_BLACKLISTED_MODEL_FILES:  "flip.py,server_app.py",
 *     VITE_RELEASE_VERSION:          "1.2.3",
 *     VITE_MAX_REIMPORT_COUNT:       "10",
 *   };
 *
 * See flip-ui/README.md for the full list of configuration variables.
 */
