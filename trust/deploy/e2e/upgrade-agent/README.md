# Upgrade agent on kind: end-to-end run

Runs the trust upgrade agent's CronJob (`autoUpgrade` in the chart, `scripts/trust_upgrade_agent.py`) against a
real Kubernetes API on a local [kind](https://kind.sigs.k8s.io/) cluster. The pieces the unit tests mock are real
here: the projected Secret, the pod's security context, the NetworkPolicies, `helm pull` from an OCI registry over
TLS, `helm upgrade` against live StatefulSets, and the agent's health checks over the cluster network.

It found six bugs in FLIP#1789 that the chart and agent tests did not, each now pinned by a test. Run it after any
change to `templates/upgrade-agent.yaml`, `templates/network-policy.yaml`, a StatefulSet template or the agent
script.

## What is real and what is not

| Real | Stand-in |
| --- | --- |
| The chart and the agent image, built from this checkout | The hub: `stub_hub.py` answers only the trust-facing calls the agent and trust-api make |
| A TLS OCI registry on the kind network, with charts pushed to it | Releases: the published `v0.15.0` service images retagged `v0.99.0` / `v0.99.1`, with trust-api re-stamped so `/health` names the release |
| The agent's RBAC, projected Secret and NetworkPolicies | Trust data: the seed hook is off |

## Run it

Needs docker, kind, kubectl, helm, openssl and python3. About 2 GB of images are pulled once.

```shell
cd trust/deploy/e2e/upgrade-agent
./run.sh setup      # cluster, registry, images, charts v0.99.0 + v0.99.1, stub hub, release at v0.99.0
./run.sh tick       # one tick now
./run.sh failure    # broken v0.99.2: expect FAILED, the site left where it is, no retry
./run.sh down       # remove everything, including the work dir
```

`REPO` (default: this checkout) picks the code under test. `WORK` (default `~/.cache/flip-kind-upgrade`) holds the
generated CA, certificates, packaged charts and the test AES key. Nothing generated is written into the checkout.

## What a pass looks like

1. `tick` after `setup`: the job log says `UPGRADE: v0.99.0 -> v0.99.1`. The hub view shows `STARTING`, then
   `SUCCEEDED`, with the lease back to `null`. The release history shows revision 2, `flip-trust-0.99.1`, `deployed`.
2. A second `tick`: `SKIP: already on v0.99.1`, with no new revision.
3. `failure` (about 11 minutes, because the agent waits `VERIFY_TIMEOUT_S` = 600 s for the new release):
   - The hub view shows `FAILED` for v0.99.2, with the detail `... Stopped; nothing was rolled back. The site was on
     v0.99.1 ...`.
   - `failed_release` is `v0.99.2`, and the newest revision is v0.99.2: the agent does not move the release back.
   - trust-api does not answer (its v0.99.2 image exits on start), which is the state an operator is left to restore.
   - The follow-up tick says `SKIP: v0.99.2 failed earlier; an operator must clear it in the portal`.

`setup` restarts the stub hub, which clears its lease, failed release and reports. To start again from v0.99.0, run
`HELM_DRIVER=configmap helm --kube-context kind-flip-upgrade -n flip-trust uninstall trust-release`, then `setup`.

## Host quirks it already handles

- **ZFS (or similar) docker root.** The kubelet cannot read rootfs stats. `kind.yaml` turns off local-storage
  capacity isolation, so the node advertises no ephemeral storage. That is why the seed hook, which requests some,
  is off.
- **A stale `ghcr.io` login in `~/.docker/config.json`.** It turns anonymous pulls of the public images into 403s.
  The images are pulled with an empty `DOCKER_CONFIG`.
- **helm treats a `localhost` registry as plain HTTP.** Charts are pushed to the registry's kind-network address
  instead, and the certificate carries that IP.
- **The chart's CI fixture AES key is a placeholder that is not 32 bytes.** trust-api cannot fingerprint it, and the
  agent refuses to upgrade a site whose key match is unknown. `setup` mints a real key for the trust and the stub hub.
- **The Helm driver is `configmap`, as the agent uses.** A release recorded as Secrets is invisible to the agent
  (FLIP#1997).
