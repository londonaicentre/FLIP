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

<a name="readme-top"></a>

# FLIP Front-End

[![flip-ui](https://img.shields.io/badge/docker-flip--ui-blue?logo=docker)](https://github.com/londonaicentre/FLIP/pkgs/container/flip-ui)

The **flip-ui** is the web-based user interface for the FLIP platform. It provides researchers and administrators with
a portal to manage projects, monitor federated learning jobs, inspect cohort query results, and retrieve trained models.

## Deployment

The flip-ui is served as a containerised static web application. In the full local stack it starts automatically with:

```bash
make central-hub   # starts flip-api, flip-db, and flip-ui
```

or as part of the full platform:

```bash
make up
```

The UI is accessible at the port defined by `FLIP_UI_PORT` in [`.env.development`](../.env.development)
(default: `3000`):

```
http://localhost:3000
```

## Configuration

The flip-ui requires the following environment variables, set via [`.env.development`](../.env.development) in
development or via the hosting environment in production:

| Variable | Description |
| --- | --- |
| `VITE_AWS_USER_POOL_ID` | AWS Cognito User Pool ID for authentication |
| `VITE_AWS_CLIENT_ID` | AWS Cognito App Client ID |
| `VITE_AWS_BASE_URL` | Base URL of the flip-api backend |
| `VITE_LOCAL` | Set to `true` for local mock mode (bypasses Cognito) |

Authentication is handled through [AWS Cognito](https://docs.aws.amazon.com/cognito/). A valid Cognito User Pool and
Client ID are required for a production deployment.

## Further Reading

- [Full FLIP Documentation](https://londonaicentreflip.readthedocs.io/en/latest/)
- [Contributing & Development Guide](CONTRIBUTING.md)
- [flip-api (backend)](../flip-api/README.md)

## License

FLIP is Apache 2.0 licensed. See the [LICENSE](../LICENSE.md) file for details.
