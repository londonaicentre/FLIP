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

# Wiring dcm2niix Job execution on Kubernetes (#565)

Status: **design + runbook**. The chart already registers the XNAT Container
Service Kubernetes backend and the `dcm2niix` command at init time
(`xnat-init-job.yaml`), but DICOM→NIfTI conversion does not yet run end-to-end
on K8s. This documents exactly why, the storage fix, and what still needs live
validation — so the remaining work is unambiguous and not re-discovered.

## What already exists

- `xnat-init-job.yaml` registers the **Kubernetes** compute backend
  (`container-service-backend-configuration.kubernetes.json`: `"backend":
  "kubernetes"`, `swarm-mode: false`) and the `dcm2niix_command.json` (mounts
  `dicom-in` → `/input`, `nifti-out` → `/output`).
- Per-project event subscriptions can be created; XNAT will *try* to spawn a
  dcm2niix Job pod when a DICOM scan arrives.

## Why it doesn't run end-to-end yet — the storage problem

When XNAT's Container Service spawns a dcm2niix **Job pod**, that pod must read
the scan's DICOM resource (`dicom-in`) and write NIfTI back (`nifti-out`). XNAT
resolves those mounts to paths under its **archive/build** directories, which in
this chart live on the `xnat-web` data PVC (mounted via `subPath: archive` /
`build` in `xnat-web.yaml`).

That PVC defaults to **`ReadWriteOnce`** (`xnat.web.persistence.accessMode`). A
spawned Job pod therefore cannot mount the same data unless **one** of:

1. **RWX storage** — the XNAT data volume uses a `ReadWriteMany` storage class
   (EFS, Longhorn, NFS, …), so a Job pod on any node can mount the archive/build
   data concurrently with `xnat-web`; **or**
2. **Single node + same-node scheduling** — the Job pod is pinned (nodeAffinity)
   to the node running `xnat-web`, where `ReadWriteOnce` permits multiple pods on
   the *same* node to mount the volume.

## Recommended fix (operator action)

For multi-node clusters, use RWX for the XNAT data volume:

```yaml
xnat:
  web:
    persistence:
      accessMode: ReadWriteMany
      storageClassName: efs-sc        # any RWX-capable provisioner
```

For a single-node cluster (e.g. k3s), `ReadWriteOnce` is sufficient **iff** the
spawned Job lands on the same node — see the open chart work below.

## What still needs doing in the chart (the actual #565 code)

The Kubernetes CS backend config schema exposed by the XNAT Container Service
plugin (3.2.0) does **not**, in this chart's current pinned plugin version,
expose a knob to inject `nodeAffinity` or an extra volume mount into the Job pods
it spawns — XNAT builds that pod spec internally. Closing #565 therefore needs,
and must be validated against a **live XNAT Container Service**:

- [ ] Confirm the spawned dcm2niix Job's pod spec (`kubectl get pod -n flip-trust
      -l <cs-job-label> -o yaml`) — which volumes/affinity XNAT actually sets.
- [ ] If XNAT mounts the build dir by host path / shared PVC: back the XNAT
      **build** directory with a RWX PVC (or the existing `shared-images` PVC)
      so both `xnat-web` and the Job mount it.
- [ ] If the plugin supports it, set Job `nodeAffinity` to the `xnat-web` node for
      the single-node / RWO case.
- [ ] If neither is exposed by the plugin: raise upstream / pin a plugin version
      that supports K8s Job volume configuration.

## Verifying end-to-end (once wired)

```bash
# Trigger via a project dcm2niix event subscription, then watch for the Job pod:
kubectl get pods -n flip-trust -l xnat.org/container-service=true -w
# It must reach Completed, write NIfTI back to the scan, and not FailedMount.
kubectl describe pod -n flip-trust <cs-job-pod>   # check Volumes / Events
```

## Related

- README "Known Limitations #1" (XNAT Container Service).
- #595 — a failing `xnat-init` hook (which registers this backend) must not block
  the rest of the deploy.
