<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

# Contributing to flip-ui

For general contribution guidelines (coding standards, pull requests, signing your work), see the
[root CONTRIBUTING.md](../CONTRIBUTING.md).

## Local development setup

### Prerequisites

- [Node.js](https://nodejs.org/) v20+
- [npm](https://docs.npmjs.com/downloading-and-installing-node-js-and-npm)

Verify your installation:

```bash
node -v   # should be v20.x or later
npm -v
```

### Install dependencies

From the `flip-ui/` directory:

```bash
npm install
```

### Start the development server

```bash
npm run dev
```

This compiles the application and starts a local dev server with hot-reload. A `predev` npm hook runs
[`scripts/generate-window-js.sh`](scripts/generate-window-js.sh) first, which writes `public/js/window.js` from the
`VITE_*` environment variables currently in scope (docker-compose populates these from `.env.development`).
`public/js/window.js` is gitignored; `public/js/window.js.example` documents the expected shape.

Configure the backend URL via `.env.development`:

```dotenv
VITE_AWS_USER_POOL_ID="<A_VALID_COGNITO_USER_POOL>"
VITE_AWS_CLIENT_ID="<A_VALID_COGNITO_CLIENT_ID>"
VITE_AWS_BASE_URL="http://localhost:8000/api"
VITE_LOCAL=false
```

Set `VITE_LOCAL=true` to enable local mock mode (bypasses Cognito authentication, useful for UI development without
a running backend).

### Build for production

```bash
npm run build        # full type-check + vite build (strict)
npm run build:deploy # vite build only — used by make deploy-ui for CloudFront S3 sync
```

The compiled output is written to `dist/`. `npm run build:deploy` skips `vue-tsc --noEmit` so it can ship while
existing type debt is worked through; `npm run build` remains the strict path for anyone cleaning that up.

## Running tests

```bash
# Unit tests
npm run test:unit

# Lint check
npm run lint
```

All tests must pass before submitting a pull request.

## Contributing changes

Please follow the process in the [root CONTRIBUTING.md](../CONTRIBUTING.md). All PRs should be linked to an issue,
pass CI checks, and include tests for new functionality.
