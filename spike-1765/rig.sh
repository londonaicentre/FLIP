#!/usr/bin/env bash
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
# Spike #1765 rig: a Flower net per project on one kind cluster (hub and trust namespaces).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/out"
K="kubectl --context kind-flip-1765"
ts() { date +%H:%M:%S; }

creds() { uv run --no-project --with cryptography "$HERE/creds.py" "$OUT"; }

base() {
  $K create namespace hub --dry-run=client -o yaml | $K apply -f -
  $K create namespace trust --dry-run=client -o yaml | $K apply -f -
  $K -n hub create secret generic flower-ca --from-file="$OUT/ca.crt" --dry-run=client -o yaml | $K apply -f -
  $K -n trust create secret generic flower-ca --from-file="$OUT/ca.crt" --dry-run=client -o yaml | $K apply -f -
}

# superlink <p>: a SuperLink for project <p>, reachable in-cluster as superlink-<p>.hub:8000.
superlink() {
  local p=$1
  $K -n hub create secret generic "superlink-$p-tls" --from-file="$OUT/superlink-$p/server.pem" \
    --from-file="$OUT/superlink-$p/server.key" --dry-run=client -o yaml | $K apply -f -
  $K -n hub apply -f - <<YAML
apiVersion: apps/v1
kind: Deployment
metadata: {name: superlink-$p, labels: {app: superlink-$p}}
spec:
  replicas: 1
  selector: {matchLabels: {app: superlink-$p}}
  template:
    metadata: {labels: {app: superlink-$p}}
    spec:
      containers:
        - name: superlink
          image: flwr/superlink:1.42.0
          args: [--ssl-ca-certfile, /ca/ca.crt, --ssl-certfile, /tls/server.pem, --ssl-keyfile, /tls/server.key,
                 --enable-supernode-auth, --host, 0.0.0.0]
          env: [{name: FLWR_DISABLE_RUNTIME_DEPENDENCY_INSTALLATION, value: "1"}]
          ports: [{containerPort: 8000}]
          readinessProbe: {tcpSocket: {port: 8000}, periodSeconds: 1}
          volumeMounts: [{name: ca, mountPath: /ca}, {name: tls, mountPath: /tls}]
      volumes:
        - {name: ca, secret: {secretName: flower-ca}}
        - {name: tls, secret: {secretName: superlink-$p-tls}}
---
apiVersion: v1
kind: Service
metadata: {name: superlink-$p}
spec: {selector: {app: superlink-$p}, ports: [{port: 8000, targetPort: 8000}]}
YAML
}

proxy() {
  $K -n hub create secret generic fleet-proxy-tls --from-file="$OUT/fleet-proxy/server.pem" \
    --from-file="$OUT/fleet-proxy/server.key" --dry-run=client -o yaml | $K apply -f -
  $K -n hub create configmap fleet-proxy-conf --from-file=fleet-proxy.conf.template="$HERE/k8s/fleet-proxy.conf.template" \
    --dry-run=client -o yaml | $K apply -f -
  $K -n hub apply -f - <<YAML
apiVersion: apps/v1
kind: Deployment
metadata: {name: fleet-proxy}
spec:
  replicas: 1
  selector: {matchLabels: {app: fleet-proxy}}
  template:
    metadata: {labels: {app: fleet-proxy}}
    spec:
      containers:
        - name: nginx
          image: flip-1765/fleet-proxy:spike
          ports: [{containerPort: 9092}]
          readinessProbe: {tcpSocket: {port: 9092}, periodSeconds: 1}
          volumeMounts:
            - {name: tls, mountPath: /certs}
            - {name: ca, mountPath: /ca}
            - {name: conf, mountPath: /etc/nginx/templates}
      volumes:
        - {name: tls, secret: {secretName: fleet-proxy-tls}}
        - {name: ca, secret: {secretName: flower-ca}}
        - {name: conf, configMap: {name: fleet-proxy-conf}}
---
apiVersion: v1
kind: Service
metadata: {name: fleet-proxy}
spec: {selector: {app: fleet-proxy}, ports: [{port: 9092, targetPort: 9092}]}
YAML
}

# operator: a pod holding the flwr CLI, the app and ~/.flwr/config.toml naming every project SuperLink.
operator() {
  $K -n hub create configmap spike-app --from-file="$HERE/app/pyproject.toml" \
    --from-file=__init__.py="$HERE/app/app/__init__.py" --from-file="$HERE/app/app/server_app.py" \
    --from-file="$HERE/app/app/client_app.py" --dry-run=client -o yaml | $K apply -f -
  $K -n hub create secret generic supernode-pubkeys --from-file="$OUT/keys/trust1-a.pub" \
    --from-file="$OUT/keys/trust1-b.pub" --dry-run=client -o yaml | $K apply -f -
  $K -n hub apply -f - <<'YAML'
apiVersion: v1
kind: Pod
metadata: {name: operator}
spec:
  containers:
    - name: flwr
      image: flwr/superlink:1.42.0
      command: [sh, -c]
      args:
        - |
          mkdir -p ~/.flwr /tmp/app/app
          for p in a b c; do printf '[superlink.%s]\naddress = "superlink-%s.hub.svc.cluster.local:8000"\nroot-certificates = "/ca/ca.crt"\n\n' $p $p; done > ~/.flwr/config.toml
          cp /src/pyproject.toml /tmp/app/ && cp /src/*.py /tmp/app/app/
          exec sleep infinity
      volumeMounts: [{name: src, mountPath: /src}, {name: ca, mountPath: /ca}, {name: pub, mountPath: /pub}]
  volumes:
    - {name: src, configMap: {name: spike-app}}
    - {name: ca, secret: {secretName: flower-ca}}
    - {name: pub, secret: {secretName: supernode-pubkeys}}
YAML
}

flwr_cli() { $K -n hub exec operator -- sh -c "cd /tmp/app && $*"; }

# register <p> <node>: the hub registers that SuperNode's PUBLIC key on project <p>'s SuperLink only.
register() { flwr_cli "flwr supernode register /pub/$2.pub $1 --format json"; }

# trust_rbac: what trust-api would hold to create and delete a project's SuperNode.
trust_rbac() {
  $K -n trust apply -f - <<'YAML'
apiVersion: v1
kind: ServiceAccount
metadata: {name: trust-api}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: supernode-manager}
rules:
  - apiGroups: [apps]
    resources: [deployments]
    verbs: [get, list, create, delete]
  - apiGroups: [""]
    resources: [secrets, configmaps]
    verbs: [get, create, delete]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: trust-api-supernode-manager}
subjects: [{kind: ServiceAccount, name: trust-api, namespace: trust}]
roleRef: {kind: Role, name: supernode-manager, apiGroup: rbac.authorization.k8s.io}
YAML
}

# supernode <p> <node> [superlink-project]: created AS trust-api. Its key from a Secret, only project <p>'s
# data, its own GPU request. Optional: [dial] another project's host and [name] the Deployment (the refusal test).
supernode() {
  local p=$1 node=$2 dial=${3:-$1} name=${4:-$2} as="--as=system:serviceaccount:trust:trust-api"
  local proxy_ip; proxy_ip=$($K -n hub get svc fleet-proxy -o jsonpath='{.spec.clusterIP}')
  $K $as -n trust create secret generic "supernode-$node" --from-file=key="$OUT/keys/$node" \
    --from-literal=TRUST_INTERNAL_SERVICE_KEY="spike-key-$node" --dry-run=client -o yaml | $K $as -n trust apply -f -
  $K $as -n trust create configmap "project-$p" --from-literal=name="$p" --dry-run=client -o yaml | $K $as -n trust apply -f -
  $K $as -n trust apply -f - <<YAML
apiVersion: apps/v1
kind: Deployment
metadata: {name: supernode-$name, labels: {app: supernode-$name}}
spec:
  replicas: 1
  selector: {matchLabels: {app: supernode-$name}}
  template:
    metadata: {labels: {app: supernode-$name}}
    spec:
      automountServiceAccountToken: false
      hostAliases: [{ip: "$proxy_ip", hostnames: ["project-$dial.fleet.flip.test"]}]
      containers:
        - name: supernode
          image: flwr/supernode:1.42.0
          args: [--root-certificates, /ca/ca.crt, --superlink, "project-$dial.fleet.flip.test:9092",
                 --auth-supernode-private-key, /keys/key]
          env:
            - {name: FLWR_DISABLE_RUNTIME_DEPENDENCY_INSTALLATION, value: "1"}
            - name: TRUST_INTERNAL_SERVICE_KEY
              valueFrom: {secretKeyRef: {name: supernode-$node, key: TRUST_INTERNAL_SERVICE_KEY}}
          resources:
            requests: {nvidia.com/gpu: "1"}
            limits: {nvidia.com/gpu: "1"}
          volumeMounts:
            - {name: ca, mountPath: /ca}
            - {name: key, mountPath: /keys}
            - {name: project, mountPath: /project}
      volumes:
        - {name: ca, secret: {secretName: flower-ca}}
        - name: key
          secret: {secretName: supernode-$node, items: [{key: key, path: key}]}
        - {name: project, configMap: {name: project-$p}}
YAML
}

# gpus <n>: advertise n fake GPUs on the kind node, so the scheduler arbitrates SuperNode pods.
gpus() {
  local node; node=$($K get nodes -o jsonpath='{.items[0].metadata.name}')
  $K patch node "$node" --subresource=status --type=json \
    -p "[{\"op\":\"add\",\"path\":\"/status/capacity/nvidia.com~1gpu\",\"value\":\"$1\"}]"
}

"$@"
