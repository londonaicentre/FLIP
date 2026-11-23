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
"""Move a trust's Helm release record from Secrets to ConfigMaps, so the upgrade agent can see it (FLIP#1997).

The in-cluster upgrade agent runs Helm with ``HELM_DRIVER=configmap`` and has no ``secrets`` verbs, so a
release recorded with Helm's default Secret driver is invisible to it. Every k8s trust deployed before
automatic upgrades existed is recorded that way.

This rewrites the release *record* only. It creates, changes or deletes no workload, Service, PVC or
StatefulSet, so no pod restarts and no volume moves. What it does, in order:

1. Reads every revision of the release from its ``sh.helm.release.v1.<release>.v<N>`` Secrets.
2. Cleans each revision, because a Secret-driver release kept the trust's passwords in two places that a
   ConfigMap must not carry (the agent can read ConfigMaps):
   - the values: ``secrets.data`` is dropped and ``secrets`` becomes ``create: false`` with
     ``existingName`` set to the live Secret;
   - the manifest: the chart's own Secret (``templates/secrets.yaml``) is removed, so Helm no longer
     treats that Secret as its own and a later upgrade with ``secrets.create=false`` leaves it alone
     rather than deleting it.
3. Marks the live trust Secret ``helm.sh/resource-policy: keep``, as a second guard against step 2.
4. Writes one ConfigMap per revision, with the same name and labels.
5. Checks that ``helm history`` reads the same revisions through both drivers, and that the ConfigMap
   records carry no ``secrets.data`` and no Secret manifest.
6. Only then deletes the Secret records.

It is a plan by default; ``--apply`` makes the changes. Running it again is safe: a release already in
ConfigMaps is reported and left alone, and a run that stopped between steps 4 and 6 is finished only when
the ConfigMaps it left match a fresh clean of the Secrets exactly. Anything else is refused.

After it, deploy that trust with ``AUTO_UPGRADE=1`` (or ``K8S_AUTO_UPGRADE=true`` in its kit), which applies
the trust Secret with kubectl and keeps it out of the release.
"""

from __future__ import annotations

import argparse
import base64
import copy
import gzip
import json
import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

PREFIX = "sh.helm.release.v1."
GZIP_MAGIC = b"\x1f\x8b\x08"
SECRET_TEMPLATE = "templates/secrets.yaml"  # pragma: allowlist secret
KEEP = "helm.sh/resource-policy"
# Helm marks an operation in flight with these; rewriting a record under one would race it.
PENDING = ("pending-install", "pending-upgrade", "pending-rollback", "uninstalling")

Runner = Callable[[list[str], str | None], str]


class Refused(Exception):
    """The migration must not run; the message says why and what to do."""


def run_command(argv: list[str], stdin: str | None = None) -> str:
    out = subprocess.run(argv, input=stdin, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise Refused(f"`{shlex.join(argv)}` failed: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout


# --- Helm's record encoding -------------------------------------------------------------------------
# A record's `release` field is base64(gzip(json)) in a ConfigMap. A Secret holds the same string, and
# the API base64s it once more as Secret data.


def decode_record(text: str) -> dict:
    raw = base64.b64decode(text)
    if raw.startswith(GZIP_MAGIC):
        raw = gzip.decompress(raw)
    return json.loads(raw)


def encode_record(release: dict) -> str:
    # mtime=0 keeps the encoding deterministic, so a resumed run can compare what it would write.
    return base64.b64encode(gzip.compress(json.dumps(release).encode(), mtime=0)).decode()


def revision_of(name: str) -> int:
    return int(name.rpartition(".v")[2])


# --- Cleaning one revision --------------------------------------------------------------------------


def _documents(manifest: str) -> list[str]:
    """The manifest's YAML documents, split the way Helm joins them (`---` lines)."""
    docs, current = [], []
    for line in manifest.splitlines(keepends=True):
        if line.rstrip() == "---":
            docs.append("".join(current))
            current = []
        else:
            current.append(line)
    docs.append("".join(current))
    return docs


def _is_chart_secret(doc: str) -> bool:
    # Helm heads every rendered template with `# Source: <chart>/<path>`; the kind check guards against a
    # future template of that name rendering something else.
    lines = [line.strip() for line in doc.splitlines()]
    from_template = any(line.startswith("# Source:") and line.endswith("/" + SECRET_TEMPLATE) for line in lines)
    return from_template and "kind: Secret" in lines


def clean_manifest(manifest: str) -> str:
    """The manifest without the chart's Secret; every other document is kept byte for byte."""
    return "---\n".join(doc for doc in _documents(manifest) if not _is_chart_secret(doc))


def clean_release(release: dict, secret_name: str) -> dict:
    """The revision as a ConfigMap may hold it: no secret values, and no chart-owned Secret."""
    out = copy.deepcopy(release)
    config = out.get("config") or {}
    config["secrets"] = {"create": False, "existingName": secret_name}
    out["config"] = config
    out["manifest"] = clean_manifest(release.get("manifest", ""))
    return out


def leaks(release: dict) -> list[str]:
    """What in a cleaned revision would still hand the agent a secret; empty when it is clean."""
    found = []
    secrets = (release.get("config") or {}).get("secrets") or {}
    if "data" in secrets or secrets.get("create"):
        found.append(f"revision {release.get('version')}: values still set secrets.data / secrets.create")
    if any(_is_chart_secret(doc) for doc in _documents(release.get("manifest", ""))):
        found.append(f"revision {release.get('version')}: manifest still renders the chart's Secret")
    return found


# --- Talking to the cluster -------------------------------------------------------------------------


@dataclass
class Cluster:
    kubectl: list[str]
    helm: list[str]
    namespace: str
    run: Runner = run_command

    def k(self, *args: str, stdin: str | None = None) -> str:
        return self.run([*self.kubectl, "-n", self.namespace, *args], stdin)

    def records(self, kind: str, release: str) -> dict[int, dict]:
        """Every record of ``release`` in ``kind`` (secrets or configmaps), by revision."""
        items = json.loads(self.k("get", kind, "-l", f"owner=helm,name={release}", "-o", "json"))["items"]
        out = {}
        for item in items:
            name = item["metadata"]["name"]
            if name.startswith(f"{PREFIX}{release}.v"):
                out[revision_of(name)] = item
        return out

    def history(self, release: str, driver: str) -> list[tuple]:
        text = self.run(
            ["env", f"HELM_DRIVER={driver}", *self.helm, "history", release, "-n", self.namespace, "-o", "json"],
            None,
        )
        fields = ("revision", "status", "chart", "app_version", "description")
        return sorted(tuple(h.get(f) for f in fields) for h in json.loads(text))


def secret_payload(item: dict) -> str:
    return base64.b64decode(item["data"]["release"]).decode()


def configmap_for(item: dict, release: dict) -> dict:
    meta = item["metadata"]
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": meta["name"], "namespace": meta["namespace"], "labels": dict(meta.get("labels") or {})},
        "data": {"release": encode_record(release)},
    }


def secret_name_of(releases: dict[int, dict], release_name: str) -> str:
    """The trust Secret the release's pods read: from the latest values, else the chart's own name."""
    latest = releases[max(releases)]
    secrets = (latest.get("config") or {}).get("secrets") or {}
    if secrets.get("create", True) is False and secrets.get("existingName"):
        return secrets["existingName"]
    # templates/secrets.yaml names it `<fullname>-secrets`; fullname is `<release>-flip-trust` unless
    # overridden, and the Makefile's TRUST_SECRET_NAME assumes the same.
    full = (latest.get("config") or {}).get("fullnameOverride") or f"{release_name}-flip-trust"
    return f"{full}-secrets"


# --- The migration ----------------------------------------------------------------------------------


def plan(cluster: Cluster, release: str) -> tuple[str, dict]:
    """``("done" | "migrate" | "resume", context)`` or raise Refused."""
    in_secrets = cluster.records("secrets", release)
    in_configmaps = cluster.records("configmaps", release)
    if not in_secrets and not in_configmaps:
        raise Refused(f"no release named {release} is recorded in namespace {cluster.namespace}")
    if not in_secrets:
        return "done", {"configmaps": sorted(in_configmaps)}

    releases = {rev: decode_record(secret_payload(item)) for rev, item in in_secrets.items()}
    pending = [f"v{rev} {r['info']['status']}" for rev, r in releases.items() if r["info"]["status"] in PENDING]
    if pending:
        raise Refused(f"a Helm operation is in flight ({', '.join(pending)}); let it finish or roll it back first")
    secret_name = secret_name_of(releases, release)
    cleaned = {rev: clean_release(r, secret_name) for rev, r in releases.items()}
    wanted = {rev: configmap_for(in_secrets[rev], cleaned[rev]) for rev in in_secrets}
    ctx = {"secrets": in_secrets, "wanted": wanted, "secret_name": secret_name}

    if not in_configmaps:
        return "migrate", ctx
    # Both stores: only a run of this script that stopped before deleting the Secrets may be finished.
    same_revisions = set(in_configmaps) == set(in_secrets)
    same_content = same_revisions and all(
        decode_record(in_configmaps[rev]["data"]["release"]) == cleaned[rev] for rev in in_secrets
    )
    if not same_content:
        raise Refused(
            f"{release} is recorded in both Secrets (revisions {sorted(in_secrets)}) and ConfigMaps "
            f"(revisions {sorted(in_configmaps)}), and the ConfigMaps are not what this migration writes. "
            "Something else wrote them; find out which record carries the live release before changing either."
        )
    return "resume", ctx


def verify(cluster: Cluster, release: str, ctx: dict) -> None:
    in_configmaps = cluster.records("configmaps", release)
    problems = []
    for rev in ctx["secrets"]:
        if rev not in in_configmaps:
            problems.append(f"revision {rev} has no ConfigMap record")
            continue
        problems += leaks(decode_record(in_configmaps[rev]["data"]["release"]))
    if problems:
        raise Refused("the ConfigMap records are not safe to switch to: " + "; ".join(problems))
    secret_history = cluster.history(release, "secret")
    configmap_history = cluster.history(release, "configmap")
    if secret_history != configmap_history:
        raise Refused(
            "helm history differs between the drivers; the Secret records were left in place.\n"
            f"  secret:    {secret_history}\n  configmap: {configmap_history}"
        )


def migrate(cluster: Cluster, release: str, apply: bool, say: Callable[[str], None] = print) -> int:
    state, ctx = plan(cluster, release)
    if state == "done":
        say(f"{release}: already recorded in ConfigMaps (revisions {ctx['configmaps']}); nothing to do.")
        return 0

    revs = sorted(ctx["secrets"])
    say(f"{release}: recorded in Secrets, revisions {revs}.")
    say(f"  trust Secret the pods read: {ctx['secret_name']} (kept; leaves the release record)")
    for rev in revs:
        say(f"  v{rev}: values lose secrets.data, manifest loses {SECRET_TEMPLATE}")
    if state == "resume":
        say("  ConfigMap records already match a clean copy: a previous run stopped before removing the Secrets.")
    if not apply:
        say("Plan only. Re-run with --apply (make migrate-release-driver ... CONFIRM=1) to make these changes.")
        return 0

    cluster.k("annotate", "secret", ctx["secret_name"], f"{KEEP}=keep", "--overwrite")
    if state == "migrate":
        for rev in revs:
            cluster.k("create", "-f", "-", stdin=json.dumps(ctx["wanted"][rev]))
        say(f"  wrote {len(revs)} ConfigMap record(s)")
    verify(cluster, release, ctx)
    say("  helm history matches through both drivers; ConfigMap records carry no secret values")
    for rev in revs:
        cluster.k("delete", "secret", ctx["secrets"][rev]["metadata"]["name"])
    say(f"  removed {len(revs)} Secret record(s). Deploy this trust with AUTO_UPGRADE=1 from now on.")
    return 0


def main(argv: list[str] | None = None, run: Runner = run_command) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--kubectl", default="kubectl", help="kubectl command, e.g. 'kubectl --context gstt'")
    parser.add_argument("--helm", default="helm", help="helm command, e.g. 'helm --kube-context gstt'")
    parser.add_argument("--apply", action="store_true", help="make the changes (default: print the plan)")
    args = parser.parse_args(argv)
    cluster = Cluster(shlex.split(args.kubectl), shlex.split(args.helm), args.namespace, run)
    try:
        return migrate(cluster, args.release, args.apply)
    except Refused as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
