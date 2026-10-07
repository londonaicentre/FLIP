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

# The MONAI bundle on-ramp is NVFLARE-only, and Flower support is deleted rather than deferred

FLIP is deliberately dual-backend nearly everywhere (`fl-apps/`, `fl-tutorials/`, `fl-services/`
and `flip-utils/flip/` all carry an `nvflare/` and a `flower/` half), so a reader will expect
`fl-apps/flower/monai_bundle/` to exist and wonder why it does not. It does not because the two
backends are not comparably ready, and there is no driver for Flower-backed bundles.

The on-ramp's central promise is that **undeclared outputs cannot leave the enclave**. That is a
data-minimisation claim, and it is the one Flower still cannot make.

Flower is no longer privacy-free: `flip/flower/privacy.py` supplies `flip_local_dp_mod`, which
clips the local update to a fixed L2 norm and adds calibrated Gaussian noise on the SuperNode. On
the narrow question of *privatising what does leave*, it is **stronger** than NVFLARE's
`PercentilePrivacy`, which sparsifies by percentile and adds no noise at all: the Flower mod is a
real (epsilon, delta) mechanism. Any argument for NVFLARE that rests on "Flower has no privacy
mechanism" is simply false, and should not be repeated.

Two differences survive that, and they are the actual reasons:

1. **Enforcement point.** NVFLARE's chain is wired server-side by `FlipFedAvgRecipe`; the uploaded
   app has no say in whether it runs. Flower's mod is opt-in *from inside the uploaded
   `client_app.py`*, which `fl-apps/flower/standard/README.md` states plainly: the template cannot
   wire it, because `client_app.py` is uploaded rather than templated, so the researcher must write
   `@app.train(mods=[flip_local_dp_mod])` themselves. A governance control that the governed
   artefact chooses whether to apply is documentation, not enforcement. For a gate whose entire
   purpose is constraining an externally-authored bundle, that distinction is the whole game.
2. **Data minimisation is still absent.** DP noise privatises the values that leave; it does not
   restrict *which tensors* leave. Flower has no `KeepOnlyVars` / `TrimBroadcastVars` equivalent, so
   "only declared outputs leave" has nothing to bind to.

The dependency model also still differs: `fl-base/Dockerfile` runs `uv sync --frozen` against
`flip-utils/uv.lock`, so an NVFLARE job cannot introduce a dependency at submission time, while
Flower's SuperNodes still launch with `--allow-runtime-dependency-installation`
(`fl-services/flower/compose.dev.yml:121,159`, `compose.secure.yml:146,193`).

We record this as **deleted, not deferred**, because a permanently-unscheduled "Phase 4b" in the
plan reads as a commitment and invites half-measures: a Flower `required_files.json` stub, a
schema field nothing consumes. If a dated driver appears, this ADR is superseded and the two
prerequisites are scoped as a project of their own.

## Consequences

- `monai_bundle` appears only in `fl-apps/nvflare/required_files.json`. `is_valid_job_type(jt,
  FLBackend.FLOWER)` returns false for it, which is the correct and self-explanatory failure.
- The adapter may assume NVFLARE. It is free to own the federated round loop directly rather than
  paying for `ClientAlgo`/`ExchangeObject`'s backend-neutrality, whose only purpose is portability
  we have decided not to buy. (Which of the two adapter shapes wins is settled by a spike; see the
  implementation plan.)
- **One finding must outlive this deletion.** Every Flower SuperNode started with
  `--allow-runtime-dependency-installation` runs `uv sync` against the job's `pyproject.toml` on
  every run, resolving from PyPI with no allowlist and no hash pinning, inside the trust enclave.
  Verified still present on `develop` at `261144f52`. That is a live exposure today, entirely
  independent of MONAI, and it is tracked as its own security issue (FLIP#1121), not as a prerequisite of work
  we are not doing.
- **A second finding, worth raising on its own.** `flip_local_dp_mod` being opt-in from the uploaded
  `client_app.py` means a Flower run today applies no client-side privacy unless the researcher
  remembered to ask for it. That is a gap in the *existing* Flower path, not something the bundle
  decision creates, and it is worth a ticket regardless of what happens to this on-ramp.
