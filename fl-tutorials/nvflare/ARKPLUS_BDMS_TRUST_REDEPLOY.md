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

# BDMS Trust node — Ark+ image update (production)

**Audience:** the operator at **Bangkok Dusit Medical Services (BDMS)** already running a FLIP trust
node (FL slot **`Trust_2`**) connected to the FLIP production Central Hub (`app.flip.aicentre.co.uk`).

**What this is:** a *lightweight redeploy* to pick up **two updated container images** needed for the
Ark+ chest-X-ray experiments. It is **NOT a re-onboarding** — your kit file, credentials, FL
participant kit, loaded data, and network allow-listing all stay exactly as they are. **Only two image
tags change.**

**When to do it:** only when the FLIP team asks you to, and **after** they confirm the production hub
has been cut over to the Ark+ images. The FLIP team will also confirm the exact two tag values to use
(see the placeholder below) — they may change once the change is merged.

---

## The two image tags (this is the whole change)

The FLIP team will give you the exact values when they coordinate your redeploy. As of this writing the
Ark+ build tags are:

| Image | Kit variable | Old value | **New value (confirm with FLIP team)** |
|-------|--------------|-----------|-----------------------------------------|
| `fl-client` (`flare-fl-client`) | `DOCKER_FL_TAG` | `stag` | `a3bf6c5bde4fac955d75858e25fc82a353345bec` |
| `imaging-api`, `trust-api`, `data-access-api`, `orthanc` **and XNAT** (`xnat-web`/`xnat-db`/`xnat-nginx` follow `DOCKER_TAG` unless the kit pins `XNAT_TAG`) | `DOCKER_TAG` | `stag` | `arkplus-platform-on-505` |

`omop-db` (pinned `latest`) and Grafana/Loki/Alloy (version-pinned) are **unchanged**. The XNAT
images at the new tag are functionally identical to the current ones (they include the
bulk-import-livelock fix) — XNAT re-pulling alongside is expected and harmless.

---

## Prerequisites (already true on your host — just confirm)

1. **NVIDIA GPU + `nvidia-container-toolkit` working.** These experiments run a `swin_large_384`
   model at 768 px and **will not run on CPU** — you need one CUDA GPU with **≥ 8 GiB VRAM**. Verify:
   ```bash
   docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
   ```
   Your kit already declares `NUM_AVAILABLE_GPUS=1`.
2. **Data present** in your OMOP database + Orthanc (unchanged — the experiments query what you
   already loaded).
3. **Logged in to the image registry:**
   ```bash
   docker login ghcr.io
   ```

---

## Redeploy — 5 steps (run on the BDMS trust host, in your FLIP checkout)

Throughout, `<HANDLE>` is the same handle you already pass to `make up-onprem-trust KIT=…` today
(your `Trust_2` kit) — you do **not** need a new kit.

**0. Check the kit file is readable by the user running `make`.** If it was extracted or written by
root, make silently loads an EMPTY kit and fails later with a misleading
`OMOP DB data dir is empty or missing: ./omop-db/volumes/Trust_/db_data` (note the missing slot
number). Fix ownership (keep the 0600 mode — the file holds your trust's secrets):
```bash
test -r trust/.env.<HANDLE> || sudo chown "$(whoami):" trust/.env.<HANDLE>
```

**1. Edit the two tag lines in the kit file you already use.** Change *only* these two lines; leave
every other line (AES key, API keys, `FL_KIT_DIR`, slot, ports, passwords) exactly as-is:
```ini
DOCKER_TAG=arkplus-platform-on-505
DOCKER_FL_TAG=a3bf6c5bde4fac955d75858e25fc82a353345bec
```

**2. Pull the new images and recreate the changed containers.** Run in a **real terminal** (the XNAT
step needs `sudo`):
```bash
env PROD=true make up-onprem-trust KIT=<HANDLE>
```
This re-pulls all images at their configured tags and recreates only `fl-client` and `imaging-api`
(the two whose tags changed). *If you prefer a clean restart:* `env PROD=true make down-onprem-trust
KIT=<HANDLE>` first, then the command above. Your data is in persistent volumes and is **not** removed.

**3. Confirm the fl-client came up on the GPU.** During start you should see:
```
🖥️  GPU passthrough on — fl-client will reserve 1 NVIDIA GPU(s)
```
and:
```bash
docker ps | grep fl-client
docker logs $(docker ps -qf name=fl-client-net-1) --tail 20   # no "num_of_gpus … exceeds available" error
```

**4. Confirm the node is still polling the hub.** Should show `200 OK` on `tasks/pending`:
```bash
docker logs $(docker ps -qf name=trust-api) --tail 3
# → GET https://app.flip.aicentre.co.uk/api/tasks/pending "HTTP/1.1 200 OK"
```

**5. Report your public egress IP to the FLIP team** (so they can re-confirm the FL-server firewall
admits it — usually unchanged if your host/network is the same):
```bash
curl -s https://api.ipify.org ; echo
```

---

## Firewall (unchanged — for reference)

Your host needs **outbound only** (no inbound ports are opened on your side):

| Host | Port | Purpose |
|------|------|---------|
| `app.flip.aicentre.co.uk` | 443 (HTTPS) | API polling |
| `fl.app.flip.aicentre.co.uk` | 8002 (gRPC/TCP) | FL training/eval traffic |

---

## Rollback

Revert the two lines to their previous values and re-run step 2:
```ini
DOCKER_TAG=stag
DOCKER_FL_TAG=stag
```

---

## Known issue during the first evaluation run (fix on the trust host)

If an evaluation reports "uploaded" but the **metrics come back empty**, and the `imaging-api` log shows
`Permission denied: '/app/data/images/net-1/…-scans-ALL.zip'`, the per-network image dir was created by
the root fl-client and `imaging-api` (uid 1000) can't write DICOMs into it. One-time fix on the trust
host (no host `sudo` needed):
```bash
docker exec -u 0 $(docker ps -qf name=imaging-api) chown -R 1000:1000 /app/data/images/net-1
```
This is durable — image cleanup only clears the directory contents, never re-creates it.

---

*Questions or a failed step → send the output of the failing command back to the FLIP team.*
