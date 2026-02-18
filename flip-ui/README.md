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

<div align="center">

  [![dev: pull requests 🧬](https://github.com/AI4VBH/flip-ui/actions/workflows/dev.yml/badge.svg)](https://github.com/AI4VBH/flip-ui/actions/workflows/dev.yml)
  [![sit: develop 🧬](https://github.com/AI4VBH/flip-ui/actions/workflows/sit.yml/badge.svg)](https://github.com/AI4VBH/flip-ui/actions/workflows/sit.yml)

</div>

<br />
<div align="center">

<h3 align="center">flip Front-End</h3>

  <p align="center">
    The flip Front-End is the UI component of flip.
    <br />
    <br />
    <a href="https://github.com/AI4VBH/flip-ui/issues">Report Bug</a>
    |
    <a href="https://github.com/AI4VBH/flip-ui/issues">Request Feature</a>
  </p>
</div>

## Getting started

Start by cloning or creating a fork of this repository. See GitHub's documentation for help with this: https://docs.github.com/en/get-started/quickstart/fork-a-repo

Secondly, ensure that you download and install [Node & NPM](https://docs.npmjs.com/downloading-and-installing-node-js-and-npm).

Once you have installed Node and NPM, you can verify that it is correctly installed and referenced in your PATH system variables by executing the following and receiving similar output:

```bash
$ node -v
v20.9.0
$ npm -v
8.3.1
```

Following the installation of Node and NPM, you should be able to run the following command to install the referenced project dependencies from the root directory of the repository:

```bash
$ npm install
```

Following the installation of the project dependencies, to begin working on the application, you can execute the following commands to start the flip Front-End.

```bash
# Compiles and hot-reloads for development
$ npm run dev

# Compiles and minifies for production
$ npm run build
```

### Customize configuration

For the flip Front-End to replicate a production environment, you will need to first override the default settings by updating the `.env.development` file in the project root directory and add/configure the following

```dotenv
VITE_AWS_USER_POOL_ID="<A_VALID_COGNITO_USER_POOL>"
VITE_AWS_CLIENT_ID="<A_VALID_COGNITO_CLIENT_ID>"
VITE_AWS_BASE_URL="https://localhost:8080/local" # <- This endpoint requires the flip APIs to be running locally.
VITE_LOCAL=false
```

The flip Front-End relies on the <a href="https://github.com/AI4VBH/flip-central-hub-api" target="_blank">flip Central API</a> for data and uses <a href="https://docs.aws.amazon.com/cognito/latest/developerguide/getting-started-with-cognito-user-pools.html" target="_blank">AWS Cognito</a> for authentication. Refer to the documentation for both to get started.

<div align="right">(<a href="#readme-top">back to top</a>)</div>

## Test

To verify any changes you make haven't affected existing functionality, you can run the unit tests and end-to-end tests, which will be required to pass for any subsequent contribution to the code base:

```bash
# Run your unit tests
$ npm run test:unit

# Lints and fixes files
$ npm run lint
```

<div align="right">(<a href="#readme-top">back to top</a>)</div>

## Contributing

Contributions are what makes the open-source community such an amazing place to learn, inspire, and create. Any contributions you make are **greatly appreciated**.

If you have a suggestion that would make this better, please fork the repo and create a pull request. You can also simply open an issue with the tag "enhancement".
Don't forget to give the project a star! Thanks again!

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

<div align="right">(<a href="#readme-top">back to top</a>)</div>

<!-- LICENSE -->
## License

flip is Apache 2.0 licensed. Please review the [LICENCE](LICENCE) for details on how the code can be used.
