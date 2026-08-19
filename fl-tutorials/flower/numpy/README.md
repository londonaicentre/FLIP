<!--
    Copyright (c) 2026 Flower Labs GmbH
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

---
tags: [quickstart]
dataset: []
framework: [numpy]
---

# Federated Learning with NumPy and Flower (Quickstart Example)

This example of Flower uses a dummy `NumPy` model as well as dummy training and evaluation steps in the `ClientApp` to showcase the core functionality of Flower apps. This app does not use a dataset.

## Set up the project

### Fetch the app

Install Flower:

```shell
pip install flwr
```

Fetch the app:

```shell
flwr new @flwrlabs/quickstart-numpy
```

```shell
numpy
├── app
│   ├── __init__.py
│   ├── client_app.py   # Defines your ClientApp
│   ├── server_app.py   # Defines your ServerApp
│   └── task.py         # Defines model creation
├── pyproject.toml      # Project metadata like dependencies and configs
└── README.md
```

### Install dependencies and project

Install the dependencies defined in `pyproject.toml` as well as the `quickstart_numpy` package.

```bash
pip install -e .
```

> **Tip:** Your `pyproject.toml` file can define more than just the dependencies of your Flower app. You can also use it to specify hyperparameters for your runs and control which Flower Runtime is used. By default, it uses the Simulation Runtime, but you can switch to the Deployment Runtime when needed.
> Learn more in the [TOML configuration guide](https://flower.ai/docs/framework/how-to-configure-pyproject-toml.html).

## Run with the Simulation Engine

In the `quickstart-numpy` directory, use `flwr run` to run a local simulation:

```bash
flwr run .
```

Refer to the [How to Run Simulations](https://flower.ai/docs/framework/how-to-run-simulations.html) guide in the documentation for advice on how to optimize your simulations.

## Run with the Deployment Engine

Follow this [how-to guide](https://flower.ai/docs/framework/how-to-run-flower-with-deployment-engine.html) to run the same app in this example but with Flower's Deployment Engine. After that, you might be interested in setting up [secure TLS-enabled communications](https://flower.ai/docs/framework/how-to-enable-tls-connections.html) and [SuperNode authentication](https://flower.ai/docs/framework/how-to-authenticate-supernodes.html) in your federation.

You can run Flower on Docker too! Check out the [Flower with Docker](https://flower.ai/docs/framework/docker/index.html) documentation.

## Differential privacy

Training updates are privatised **on the SuperNode**, before the reply leaves the trust. The
`flip_local_dp_mod` mod from
[`flip.flower.privacy`](../../../flip-utils/flip/flower/privacy.py) clips the local update to a
fixed L2 norm and adds Gaussian noise scaled to the configured budget:

```
sigma = dp-sensitivity * sqrt(2 * ln(1.25 / dp-delta)) / dp-epsilon
```

It is wired in `app/client_app.py` as `@app.train(mods=[flip_local_dp_mod])`, so it covers training
rounds only — `@app.evaluate` is untouched. That mirrors the scope of the NVFLARE apps'
`PercentilePrivacy` result filter, though the mechanism is stronger: NVFLARE sparsifies by
percentile and adds no noise, while this is a real (epsilon, delta) mechanism built on Flower's own
`compute_clip_model_update` / `add_gaussian_noise_inplace`.

| Key                | Default | Meaning |
|--------------------|---------|---------|
| `dp-enabled`       | `true`  | Master switch. `false` makes the mod a pass-through, so DP-on / DP-off runs use an identical app |
| `dp-clipping-norm` | `1.0`   | L2 norm the update is clipped to before noise |
| `dp-sensitivity`   | `1e-4`  | How much one training example can move the update |
| `dp-epsilon`       | `10.0`  | Privacy budget — smaller means more privacy and more noise |
| `dp-delta`         | `1e-5`  | Probability the guarantee fails outright |

Override per run without editing the app:

```bash
flwr run . --run-config "dp-enabled=false"
flwr run . --run-config "dp-epsilon=1.0 dp-clipping-norm=0.5"
```

> ⚠️ **The defaults are demonstration values, chosen utility-first** so this tutorial still
> converges with the mechanism live (they give sigma ≈ 4.8e-5). They are **not** a defensible
> privacy budget. A real one calibrates `dp-sensitivity` to the local dataset — roughly
> `2 * dp-clipping-norm / |D|` for an average-of-examples update — and accounts for composition
> across rounds, which this mod does not do: every round spends the budget again. With only a
> handful of trusts the noise also does not average down the way central DP's does.

## Resources

- Flower website: [flower.ai](https://flower.ai/)
- Check the documentation: [flower.ai/docs](https://flower.ai/docs/)
- Give Flower a ⭐️ on GitHub: [GitHub](https://github.com/adap/flower)
- Join the Flower community!
  - [Flower Slack](https://flower.ai/join-slack/)
  - [Flower Discuss](https://discuss.flower.ai/)
