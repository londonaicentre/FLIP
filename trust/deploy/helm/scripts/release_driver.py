# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Say which storage driver a Helm release is recorded with, and what that means for auto-upgrade.

Helm records a release in either a Secret (its default) or a ConfigMap (``HELM_DRIVER=configmap``).
The in-cluster upgrade agent runs with the ConfigMap driver, because that is the identity the chart
grants it — no ``secrets`` verb — and a Helm client cannot see a release recorded the other way: it
does not find it, and the agent fails the tick rather than installing a second release underneath
the running one.

That is the safe failure, but it is a silent one for an operator: the agent reports FAILED and the
site has no idea why. This is the read-only answer, run before pointing an agent at a trust::

    python3 trust/deploy/helm/scripts/release_driver.py --release flip-trust --namespace flip-trust

It reads both storage locations, prints what it found, and exits non-zero when the answer is
"Nothing this agent can see". It writes nothing: rewriting a release record is a step for a
disposable cluster, not something a report script should do to a live site holding a trust's data.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Iterable

PREFIX = "sh.helm.release.v1."
SECRET, CONFIGMAP, NONE = "secret", "configmap", "none"


def release_names(objects: Iterable[dict], kind: str) -> dict[str, int]:
    """Every release recorded in ``objects``, with its highest version number.

    Helm names the record ``sh.helm.release.v1.<release>.v<revision>`` and labels it
    ``owner=helm``; the name is what this reads, because the label selector alone cannot tell one
    release from another and the two storage kinds use the same naming.
    """
    latest: dict[str, int] = {}
    for item in objects:
        name = item.get("metadata", {}).get("name", "")
        if not name.startswith(PREFIX):
            continue
        tail = name[len(PREFIX) :]
        release, _, revision = tail.rpartition(".v")
        if not release or not revision.isdigit():
            continue
        latest[release] = max(latest.get(release, 0), int(revision))
    return latest


def describe(release: str, in_secrets: dict[str, int], in_configmaps: dict[str, int]) -> tuple[str, str]:
    """The driver to use for ``release`` and a sentence saying why."""
    secret_revision, configmap_revision = in_secrets.get(release), in_configmaps.get(release)
    if configmap_revision and secret_revision:
        # Both, from an interrupted migration. The higher revision is the one Helm would pick
        # within its own driver; naming both is what an operator needs in order to decide.
        return CONFIGMAP, (
            f"recorded twice: a ConfigMap at revision {configmap_revision} and a Secret at {secret_revision} — "
            "a migration was started and not finished. The agent will use the ConfigMap record; check which "
            "one carries the live values before trusting either"
        )
    if configmap_revision:
        return CONFIGMAP, f"recorded in ConfigMaps at revision {configmap_revision} — the agent can upgrade this trust"
    if secret_revision:
        return SECRET, (
            f"recorded in Secrets at revision {secret_revision} — the agent cannot see this release and will "
            "report FAILED rather than install a second one; the migration has to run first"
        )
    return NONE, "no release of that name is recorded in either store"


# Only the object names: a Helm release Secret carries the whole release (every value, the trust's
# passwords included), and the answer here needs nothing but the record's name.
_NAMES_ONLY = ["-o", "custom-columns=NAME:.metadata.name", "--no-headers"]


def _kubectl_names(kubectl: str, args: list[str]) -> list[dict]:
    """The listed objects' names, in the shape ``release_names`` reads, without fetching their data."""
    out = subprocess.run([kubectl, *args, *_NAMES_ONLY], capture_output=True, text=True, check=True)
    return [{"metadata": {"name": name}} for name in out.stdout.split() if name]


def records(namespace: str, kubectl: str = "kubectl") -> tuple[dict[str, int], dict[str, int]]:
    """Every release recorded in this namespace's Secrets and ConfigMaps, with its latest revision."""
    secrets = _kubectl_names(kubectl, ["get", "secrets", "-n", namespace, "-l", "owner=helm"])
    configmaps = _kubectl_names(kubectl, ["get", "configmaps", "-n", namespace, "-l", "owner=helm"])
    return release_names(secrets, "secrets"), release_names(configmaps, "configmaps")


def report(release: str, namespace: str, kubectl: str = "kubectl") -> tuple[str, str]:
    """Read both stores with ``kubectl`` and describe where ``release`` is recorded."""
    return describe(release, *records(namespace, kubectl))


def deploy_refusal(release: str, driver: str, in_secrets: dict[str, int], in_configmaps: dict[str, int]) -> str | None:
    """Why ``helm upgrade --install`` with ``driver`` must not run, or None when it is safe.

    A Helm client cannot see a release recorded with the other driver, so ``--install`` would put a
    second release on top of the running one. A deploy may use ``driver`` only when the release is
    recorded with that driver alone, or not at all (a first install).
    """
    other = in_secrets if driver == CONFIGMAP else in_configmaps
    mine = in_configmaps if driver == CONFIGMAP else in_secrets
    if release not in other:
        return None
    if release in mine:
        return (
            f"{release} is recorded in both Secrets (revision {in_secrets[release]}) and ConfigMaps "
            f"(revision {in_configmaps[release]}): a driver migration was started and not finished. "
            "Finish it before deploying."
        )
    if driver == CONFIGMAP:
        return (
            f"{release} is recorded in Secrets (revision {in_secrets[release]}), so a deploy with automatic "
            "upgrades (HELM_DRIVER=configmap) would not see it and would install a second release on top. "
            "Move the release record to ConfigMaps first (README, 'The deploy side has to agree on the driver'), "
            "or deploy without AUTO_UPGRADE."
        )
    return (
        f"{release} is recorded in ConfigMaps (revision {in_configmaps[release]}): this trust deploys with "
        "automatic upgrades. Deploy with AUTO_UPGRADE=1 (or K8S_AUTO_UPGRADE=true in the kit), or the "
        "Secret-driver client would install a second release on top."
    )


def main(argv: list[str] | None = None, run: Callable[..., tuple[str, str]] = report) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release", default="flip-trust")
    parser.add_argument("--namespace", default="flip-trust")
    parser.add_argument("--kubectl", default="kubectl")
    parser.add_argument(
        "--deploy-driver",
        choices=(SECRET, CONFIGMAP),
        help="check that a deploy with this driver is safe instead of reporting; exit 1 with the reason if not",
    )
    args = parser.parse_args(argv)

    if args.deploy_driver:
        try:
            refusal = deploy_refusal(args.release, args.deploy_driver, *records(args.namespace, args.kubectl))
        except subprocess.CalledProcessError as exc:
            print(f"could not read the release records: {exc}", file=sys.stderr)
            return 2
        if refusal:
            print(f"❌ {refusal}", file=sys.stderr)
            return 1
        return 0

    try:
        driver, why = run(args.release, args.namespace, args.kubectl)
    except subprocess.CalledProcessError as exc:
        print(f"could not read the release records: {exc}", file=sys.stderr)
        return 2
    print(f"{args.release}: {driver}")
    print(why)
    # 1 is "this trust needs the migration", which is the answer an operator acts on.
    return 0 if driver in (CONFIGMAP,) else 1


if __name__ == "__main__":
    raise SystemExit(main())
