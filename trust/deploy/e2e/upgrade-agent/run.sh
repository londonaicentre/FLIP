#!/usr/bin/env bash
#
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
# End-to-end run of the trust upgrade agent's CronJob on a kind cluster (FLIP#1644, FLIP#1997).
# See README.md. Stages (idempotent, run in this order):
#   ./run.sh setup      cluster + TLS registry + images + charts + stub hub + release at v0.99.0
#   ./run.sh tick       run one CronJob tick now; show its log, the hub's view and the release history
#   ./run.sh failure    publish a broken v0.99.2, point the hub at it, tick: expect FAILED, left in place
#   ./run.sh state      hub state, release history, pods
#   ./run.sh down       delete the cluster, the registry and the work dir
# Needs: docker, kind, kubectl, helm, openssl, python3.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# The checkout under test (the chart, the agent Dockerfile and script all come from it).
REPO="${REPO:-$(cd "$HERE/../../../.." && pwd)}"
# Generated state (CA, registry certs, packaged charts, the test AES key) lives outside the checkout.
WORK="${WORK:-${XDG_CACHE_HOME:-$HOME/.cache}/flip-kind-upgrade}"
mkdir -p "$WORK"
CHART="$REPO/trust/deploy/helm"
CLUSTER=flip-upgrade
CTX="kind-$CLUSTER"
NS=flip-trust
REL=trust-release
REG=kind-registry
REG_IP=""   # set in setup from the kind network's IPv4 subnet
BASE_TAG="${BASE_TAG:-v0.15.0}"
FROM=v0.99.0
TO=v0.99.1
BAD=v0.99.2   # the failure stage's deliberately broken release
K="kubectl --context $CTX"
export HELM_DRIVER=configmap   # the agent's driver: a Secret-driver release is invisible to it

# Public GHCR images, pulled anonymously: a stale ghcr.io login in ~/.docker turns them into 403s.
ANON="$WORK/.docker-anon"; mkdir -p "$ANON"

log() { printf '\n=== %s\n' "$*"; }

certs() {
  if [ -f "$WORK/certs/server.crt" ] && openssl x509 -in "$WORK/certs/server.crt" -noout -ext subjectAltName | grep -q "$REG_IP"; then return; fi
  rm -rf "$WORK/certs"; docker rm -f "$REG" >/dev/null 2>&1 || true
  mkdir -p "$WORK/certs" && cd "$WORK/certs"
  openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=flip-kind-test-ca" -keyout ca.key -out ca.crt 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj "/CN=$REG" -keyout server.key -out server.csr 2>/dev/null
  printf 'subjectAltName=DNS:%s,DNS:localhost,IP:127.0.0.1,IP:%s\n' "$REG" "$REG_IP" > san.ext
  openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 30 -extfile san.ext -out server.crt 2>/dev/null
  cd - >/dev/null
}

setup() {
  log "kind cluster"
  kind get clusters | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --config "$HERE/kind.yaml" --wait 120s
  subnet="$(docker network inspect kind -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' | tr ' ' '\n' | grep -m1 '\.')"
  REG_IP="${subnet%.*.*}.0.250"
  certs

  log "TLS registry on the kind network ($REG / $REG_IP)"
  if ! docker inspect "$REG" >/dev/null 2>&1; then
    docker run -d --restart=always --name "$REG" -p 127.0.0.1:5443:443 \
      -v "$WORK/certs:/certs:ro" -e REGISTRY_HTTP_ADDR=0.0.0.0:443 \
      -e REGISTRY_HTTP_TLS_CERTIFICATE=/certs/server.crt -e REGISTRY_HTTP_TLS_KEY=/certs/server.key registry:2 >/dev/null
    docker network connect --ip "$REG_IP" kind "$REG"
  fi

  log "images: $BASE_TAG retagged as $FROM and $TO (trust-api re-stamped so /health names the release)"
  for svc in data-access-api imaging-api omop-db orthanc trust-api; do
    DOCKER_CONFIG="$ANON" docker pull -q "ghcr.io/londonaicentre/$svc:$BASE_TAG" >/dev/null
    for t in $FROM $TO; do
      if [ "$svc" = trust-api ]; then
        printf 'FROM ghcr.io/londonaicentre/trust-api:%s\nENV FLIP_RELEASE=%s\n' "$BASE_TAG" "$t" \
          | docker build -q -t "ghcr.io/londonaicentre/trust-api:$t" - >/dev/null
      else
        docker tag "ghcr.io/londonaicentre/$svc:$BASE_TAG" "ghcr.io/londonaicentre/$svc:$t"
      fi
    done
  done
  # The trust-seed hook's uv image (a pinned digest) is public; the kind node pulls it itself.

  log "agent image from the branch's Dockerfile, plus the test CA (the only test-only layer)"
  docker build -q -f "$CHART/agent/Dockerfile" -t trust-upgrade-agent:branch "$REPO" >/dev/null
  printf 'FROM trust-upgrade-agent:branch\nUSER 0\nCOPY ca.crt /usr/local/share/ca-certificates/flip-kind-test-ca.crt\nRUN update-ca-certificates >/dev/null 2>&1\nUSER 65534:65534\n' \
    | docker build -q -t ghcr.io/londonaicentre/trust-upgrade-agent:test -f - "$WORK/certs" >/dev/null
  docker build -q -t stub-hub:test -f - "$HERE" >/dev/null <<'EOF'
FROM python:3.12-alpine
COPY stub_hub.py /stub_hub.py
CMD ["python3", "-u", "/stub_hub.py"]
EOF

  log "kind load"
  imgs=(stub-hub:test ghcr.io/londonaicentre/trust-upgrade-agent:test)
  for svc in data-access-api imaging-api omop-db orthanc trust-api; do imgs+=("ghcr.io/londonaicentre/$svc:$FROM" "ghcr.io/londonaicentre/$svc:$TO"); done
  kind load docker-image --name "$CLUSTER" "${imgs[@]}" >/dev/null

  log "charts $FROM and $TO from the branch, pushed to oci://$REG/charts"
  rm -rf "$WORK/charts" && mkdir -p "$WORK/charts"
  for t in $FROM $TO; do helm package "$CHART" --version "${t#v}" --app-version "$t" -d "$WORK/charts" >/dev/null; done
  # By its kind-network address: helm treats a localhost registry as plain HTTP.
  for f in "$WORK"/charts/*.tgz; do helm push "$f" "oci://$REG_IP/charts" --ca-file "$WORK/certs/ca.crt"; done

  log "stub hub"
  $K create namespace hub --dry-run=client -o yaml | $K apply -f - >/dev/null
  # A real AES-256 key, shared by the stub hub and the trust Secret below. The chart's CI fixture
  # holds a placeholder that decodes to 38 bytes, which trust-api cannot fingerprint, so
  # hub_key_match stays null and the agent (rightly) refuses to upgrade.
  [ -f "$WORK/aes.key" ] || { umask 077; head -c 32 /dev/urandom | base64 -w0 > "$WORK/aes.key"; }
  aes="$(cat "$WORK/aes.key")"
  apikey="$(sed -n 's/^ *trust-api-key: *"\{0,1\}\([^"]*\)"\{0,1\} *$/\1/p' "$CHART/ci/test-secret.yaml" | head -1)"
  [ -n "$apikey" ] || { echo "no trust-api-key in $CHART/ci/test-secret.yaml" >&2; exit 1; }
  $K -n hub create secret generic stub-hub --from-literal=AES_KEY_BASE64="$aes" --from-literal=TRUST_API_KEY="$apikey" \
    --dry-run=client -o yaml | $K apply -f - >/dev/null
  $K apply -f - >/dev/null <<EOF
apiVersion: apps/v1
kind: Deployment
metadata: {name: stub-hub, namespace: hub}
spec:
  replicas: 1
  selector: {matchLabels: {app: stub-hub}}
  template:
    metadata: {labels: {app: stub-hub}}
    spec:
      containers:
        - name: hub
          image: stub-hub:test
          imagePullPolicy: Never
          envFrom: [{secretRef: {name: stub-hub}}]
          env: [{name: TARGET_RELEASE, value: "$TO"}, {name: TRUST_API_KEY_HEADER, value: Authorization}]
          ports: [{containerPort: 80}]
---
apiVersion: v1
kind: Service
metadata: {name: stub-hub, namespace: hub}
spec: {selector: {app: stub-hub}, ports: [{port: 80, targetPort: 80}]}
EOF
  # A fresh hub process on every setup: its lease, failed_release and reports live in memory.
  $K -n hub rollout restart deploy/stub-hub >/dev/null
  $K -n hub rollout status deploy/stub-hub --timeout=120s

  log "trust release at $FROM (chart from the registry, configmap driver)"
  $K create namespace "$NS" --dry-run=client -o yaml | $K apply -f - >/dev/null
  $K label namespace "$NS" app.kubernetes.io/managed-by=Helm --overwrite >/dev/null
  $K annotate namespace "$NS" meta.helm.sh/release-name=$REL meta.helm.sh/release-namespace=$NS --overwrite >/dev/null
  $K apply -f "$CHART/ci/test-secret.yaml" >/dev/null
  $K -n "$NS" patch secret flip-trust-secrets --type merge \
    -p "{\"data\":{\"aes-key-base64\":\"$(printf %s "$(cat "$WORK/aes.key")" | base64 -w0)\"}}" >/dev/null
  if helm --kube-context "$CTX" -n "$NS" status "$REL" 2>/dev/null | grep -q "STATUS: failed"; then
    helm --kube-context "$CTX" -n "$NS" uninstall "$REL" --wait >/dev/null
  fi
  if ! helm --kube-context "$CTX" -n "$NS" status "$REL" >/dev/null 2>&1; then
    helm --kube-context "$CTX" install "$REL" "oci://$REG_IP/charts/flip-trust" --version "${FROM#v}" \
      --ca-file "$WORK/certs/ca.crt" -n "$NS" -f "$CHART/ci/test-values.yaml" \
      --set secrets.existingName=flip-trust-secrets \
      --set trustApi.env.CENTRAL_HUB_API_URL=http://stub-hub.hub.svc.cluster.local/api \
      --set trustApi.env.TRUST_API_KEY_HEADER=Authorization \
      --set global.image.tag=$FROM \
      --set trustApi.image.pullPolicy=IfNotPresent --set imagingApi.image.pullPolicy=IfNotPresent \
      --set dataAccessApi.image.pullPolicy=IfNotPresent --set omopDb.image.pullPolicy=IfNotPresent \
      --set orthanc.image.pullPolicy=IfNotPresent \
      --set trustName=KIND1 --set environment=production \
      --set autoUpgrade.enabled=true --set autoUpgrade.suspend=true \
      --set autoUpgrade.image.tag=test --set autoUpgrade.image.pullPolicy=Never \
      --set autoUpgrade.chartRepository=oci://$REG_IP/charts/flip-trust \
      --set trustData.seed.enabled=false \
      --timeout 15m --wait
  fi
  state
}

tick() {
  local job
  job="tick-$(date +%H%M%S)"
  log "tick: $job (from the CronJob's own spec)"
  $K -n "$NS" create job "$job" --from=cronjob/"$($K -n "$NS" get cronjob -o name | head -1 | cut -d/ -f2)" >/dev/null
  # One `kubectl wait` cannot wait for "complete OR failed"; poll the Job's own counters instead.
  for _ in $(seq 1 480); do
    st="$($K -n "$NS" get "job/$job" -o jsonpath='{.status.succeeded}/{.status.failed}' 2>/dev/null || echo gone)"
    case "$st" in 1/*|*/1|gone) break ;; esac
    sleep 5
  done
  $K -n "$NS" logs "job/$job" --tail=200 || true
  state
}

state() {
  log "hub view"
  $K -n hub exec deploy/stub-hub -- wget -qO- http://127.0.0.1/_state | python3 -m json.tool || true
  log "release history"
  helm --kube-context "$CTX" -n "$NS" history "$REL" 2>&1 | tail -5
  log "pods"
  $K -n "$NS" get pods -o wide 2>&1 | head -30
}

failure() {
  [ -n "$REG_IP" ] || { subnet="$(docker network inspect kind -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' | tr ' ' '\n' | grep -m1 '\.')"; REG_IP="${subnet%.*.*}.0.250"; }
  log "images $BAD: trust-api exits at start, the rest retagged"
  for svc in data-access-api imaging-api omop-db orthanc; do
    docker tag "ghcr.io/londonaicentre/$svc:$BASE_TAG" "ghcr.io/londonaicentre/$svc:$BAD"
  done
  printf 'FROM ghcr.io/londonaicentre/trust-api:%s\nENV FLIP_RELEASE=%s\nENTRYPOINT ["sh","-c","echo broken on purpose; exit 1"]\nCMD []\n' "$BASE_TAG" "$BAD" \
    | docker build -q -t "ghcr.io/londonaicentre/trust-api:$BAD" - >/dev/null
  local bad_imgs=()
  for svc in data-access-api imaging-api omop-db orthanc trust-api; do bad_imgs+=("ghcr.io/londonaicentre/$svc:$BAD"); done
  kind load docker-image --name "$CLUSTER" "${bad_imgs[@]}" >/dev/null

  log "chart $BAD"
  helm package "$CHART" --version "${BAD#v}" --app-version "$BAD" -d "$WORK/charts" >/dev/null
  helm push "$WORK/charts/flip-trust-${BAD#v}.tgz" "oci://$REG_IP/charts" --ca-file "$WORK/certs/ca.crt"

  log "hub target -> $BAD"
  $K -n hub exec deploy/stub-hub -- wget -qO- --header 'Content-Type: application/json' \
    --post-data "{\"target_release\": \"$BAD\"}" http://127.0.0.1/_policy >/dev/null

  local start; start=$(date +%s)
  tick
  log "tick took $(( $(date +%s) - start ))s; trust-api /health now"
  $K -n "$NS" exec "deploy/$REL-flip-trust-trust-api" -- python3 -c \
    'import urllib.request;print(urllib.request.urlopen("http://127.0.0.1:8000/health").read().decode())' || true
  log "a second tick must not retry $BAD"
  tick
}

down() {
  kind delete cluster --name "$CLUSTER" || true
  docker rm -f "$REG" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}

"${1:-setup}"
