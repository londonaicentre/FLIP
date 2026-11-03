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

# The MONAI bundle on-ramp accepts pure-config bundles only

A MONAI Bundle config is executable code, not declarative data: `ConfigComponent.instantiate()`
resolves `_target_` strings through `pydoc.locate()` (stdlib's import-anything-by-dotted-path)
and `ConfigExpression.evaluate()` runs `$`-prefixed strings through `eval()`. MONAI ships no
allowlist mechanism and its own documentation presents arbitrary third-party imports as expected
usage. FLIP therefore enforces its own **governance profile** (`monai-flip-v0.1`) at seal time,
and v0.1 **hard-rejects any `.py` file inside the bundle upload**, including the `scripts/`
directory the MONAI bundle layout permits.

The rejection is what makes the security claim provable. With no bundle-shipped Python, every
executable reference in an approved bundle resolves to a package already installed in the frozen
`fl-base` image, so allowlisting `_target_` strings is meaningful rather than theatre. Allow
`scripts/` and `_target_: scripts.transforms.MyTransform` moves the code out of the analyser's
reach into a `.py` file that only an advisory Bandit pass ever looks at, at which point the
bundle path is no stronger than the hand-written app path it is meant to improve on.

## FLIP's own exporter emits the shape this rejects

`flip/export/bundle.py` writes a directory-form bundle whose `scripts/` directory holds the
application's own Python (`_SCRIPTS_DIR_NAME`, `_SCRIPTS_COPY_EXCLUDES`). So FLIP produces bundles
that this ADR would refuse on the way back in. That is deliberate, not an oversight:

- The two directions carry different trust. The exporter packages code that FLIP already ran, under
  an app that passed upload review and a training run; and it packages it *for inference elsewhere*,
  outside any trust enclave. The importer receives code from outside, to execute *inside* one.
- The round trip is not closed anyway (see `flip-utils/CONTEXT.md`): the exporter
  writes **inference** bundles, the on-ramp consumes **training** bundles. A directory-form export
  cannot be fed to the on-ramp regardless of the `scripts/` rule, because it carries no
  `configs/train.json`.

The practical consequence is a documentation one: "FLIP exports MONAI bundles and FLIP imports MONAI
bundles" must never be written without the qualifier, or someone will reasonably expect an exported
bundle to be re-importable and be told their own platform's output is rejected.

## The profile is a floor, not a setting

FLIP core engineering owns and versions the baseline profile, shipped inside the image, and the
hub's seal checks every bundle against it. Nobody can widen it, so "passed `monai-flip-v0.1`" means
the same thing everywhere.

A trust that distrusts a component narrows the profile **at the trust**, in its own governance
document (FLIP#1259): a `[monai_bundle]` section with deny keys only (`deny_targets`,
`deny_bundles`), enforced in the fl-client before the adapter instantiates anything, and logged
there with the effective profile hash. There is deliberately no allow key, and no hub-side
per-deployment overlay: the trust governance design rules that the hub never pushes policy to a
trust, because a hub that both sets and reports a trust's policy proves nothing about it.

This mirrors FLIP's existing asymmetry rule for cohort-query validation: a trust must stay safe
regardless of what the hub checked. The hub seal is fast feedback against the floor; the trust's
check is authoritative for its own narrowing.

## Considered options

- **Allow `scripts/` behind a blocking Bandit profile.** Rejected: Bandit is a denylist of known-bad
  patterns built to lint code you wrote, not to confine code you distrust. It fires on neither
  `$__import__('os').system(...)` nor `open('/etc/shadow').read()`. Gating on it would make the
  control look stronger than it is, which is worse than not having it.
- **Allow `scripts/` with human review only.** Rejected as *dishonest rather than unsafe*: it is
  today's posture exactly, so the plan's claim of "reduced custom-code exposure for clinical
  enclaves" would be false for any bundle shipping `scripts/`.
- **Symbol-level enumeration of every permitted `_target_`.** Rejected: MONAI ships ~300 transforms
  alone; every minor release and most new bundles would need an allowlist PR. The chosen form is
  submodule-prefix allow (`monai.transforms.*`, `monai.networks.*`, `torch.nn.*`, `ignite.*`, …)
  with a leaf denylist that wins over the prefix (`torch.load`, `torch.jit.load`,
  `torch.serialization.*`, `torch.hub.*`, `monai.apps.datasets.*`, `monai.apps.utils.download_*`),
  plus a restricted-grammar `ast` visitor for `$`-expressions.

## Consequences

- A visible slice of the MONAI Model Zoo is not importable at v0.1. This is the cost, and it should
  be stated in the user documentation rather than discovered at upload.
- The analyser never evaluates anything: `_target_` strings are collected lexically from the
  macro-resolved config tree, `@refs` point at sibling items the walk already covers, and
  `$`-expressions are parsed with `ast`. `get_parsed_content()` is never called.
- Widening later (adding a vetted `scripts/` path) is a policy change, not a redesign; the seal
  step and analyser are already the place it would land.
