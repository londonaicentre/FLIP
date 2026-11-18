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
#
# Copy a local folder onto a cluster node's filesystem, for `make stage-kit` and `make stage-vocab` (FLIP#1958).
# The chart mounts the FL kit and the OMOP vocabulary from the node by hostPath; nothing in the cluster fetches them.
#
#   stage-to-node.sh --context <ctx> --src <dir> --dest <node path> [--subdirs "a b"] (--owner <uid> | --world-readable)
#
# How it reaches the node:
#   - a kind node is a Docker container of the same name: `docker exec` / `docker cp`, as before;
#   - any other node (k3s, a VM, bare metal): a short-lived root pod pinned to the node mounts <dest> as a hostPath,
#     and the folder goes in with `kubectl cp`. Neither docker nor root on the host is needed, only RBAC to create
#     that pod in $NAMESPACE. A namespace under Pod Security "baseline" or stricter refuses it; there use the kit
#     Secret route (flClient.kit.source=secret) or copy the folder by the site's own means.
#
# The same steps on both paths, in this order: create <dest>, empty it (both copies merge, so a re-stage would keep
# the previous kit's files beside the new ones), copy <src>'s contents, create the --subdirs (a subPath missing from
# the hostPath is created by kubelet as root 0700, which the non-root client cannot write), then the permissions:
# --owner <uid> makes it private to that uid (chmod -R go-rwx, chown -R uid:uid), --world-readable runs chmod -R a+rX.
#
# Env: NAMESPACE (default flip-trust), KUBECTL (default kubectl), DOCKER (default docker),
#      STAGE_TIMEOUT for the pod to start (default 180s).
set -euo pipefail

# FLIP's mirror of busybox (it has the tar kubectl cp needs), the chart's xnat.web.initImage; a test keeps them equal.
STAGE_IMAGE="ghcr.io/londonaicentre/mirror/library/busybox:1.36@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662"

context="" src="" dest="" subdirs="" owner="" world_readable=""
while [ $# -gt 0 ]; do
  case "$1" in
    --context) context="$2"; shift 2 ;;
    --src) src="$2"; shift 2 ;;
    --dest) dest="$2"; shift 2 ;;
    --subdirs) subdirs="$2"; shift 2 ;;
    --owner) owner="$2"; shift 2 ;;
    --world-readable) world_readable=1; shift ;;
    *) echo "❌ unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$context" ] || { echo "❌ --context is required: this writes to a node's filesystem" >&2; exit 2; }
[ -d "$src" ] || { echo "❌ --src $src is not a directory" >&2; exit 2; }
case "$dest" in
  /?*) ;;
  *) echo "❌ --dest must be an absolute path other than / (it is emptied before the copy): '$dest'" >&2; exit 2 ;;
esac
case "$owner" in
  '') [ -n "$world_readable" ] || { echo "❌ say who reads it: --owner <uid> or --world-readable" >&2; exit 2; } ;;
  *[!0-9]*) echo "❌ --owner must be a numeric uid: '$owner'" >&2; exit 2 ;;
esac

namespace="${NAMESPACE:-flip-trust}"
read -r -a kubectl <<< "${KUBECTL:-kubectl}"
kubectl+=(--context "$context")
docker="${DOCKER:-docker}"

node=$("${kubectl[@]}" get nodes -o jsonpath='{.items[0].metadata.name}') && [ -n "$node" ] || {
  echo "❌ no node found in context $context: is the cluster reachable?" >&2
  exit 1
}

if [ -n "$owner" ]; then
  permissions="chmod -R go-rwx \"\$1\" && chown -R $owner:$owner \"\$1\""
else
  permissions="chmod -R a+rX \"\$1\""
fi
# Runs on the node, with $1 the destination: everything after the copy.
finish="set -e${subdirs:+; for d in $subdirs; do mkdir -p \"\$1/\$d\"; done}; $permissions"

kind_cluster=""
if command -v "$docker" > /dev/null 2>&1; then
  kind_cluster=$("$docker" inspect -f '{{index .Config.Labels "io.x-k8s.kind.cluster"}}' "$node" 2> /dev/null || true)
fi

if [ -n "$kind_cluster" ]; then
  echo "📦 $src → kind node $node:$dest (docker cp)"
  "$docker" exec "$node" mkdir -p "$dest"
  "$docker" exec "$node" find "$dest" -mindepth 1 -delete
  "$docker" cp "$src/." "$node:$dest/"
  "$docker" exec "$node" sh -c "$finish" sh "$dest"
  exit 0
fi

pod="flip-stage-$(date +%s)-$$"
echo "📦 $src → node $node:$dest (pod $namespace/$pod, kubectl cp)"
"${kubectl[@]}" create namespace "$namespace" --dry-run=client -o yaml | "${kubectl[@]}" apply -f - > /dev/null
trap '"${kubectl[@]}" -n "$namespace" delete pod "$pod" --ignore-not-found --wait=false > /dev/null 2>&1 || true' EXIT
"${kubectl[@]}" -n "$namespace" apply -f - > /dev/null << YAML
apiVersion: v1
kind: Pod
metadata:
  name: $pod
  labels:
    app.kubernetes.io/name: flip-stage-to-node
spec:
  nodeName: $node
  restartPolicy: Never
  tolerations:
    - operator: Exists
  containers:
    - name: stage
      image: $STAGE_IMAGE
      command: ["sleep", "900"]
      securityContext:
        runAsUser: 0
      volumeMounts:
        - name: dest
          mountPath: /stage
  volumes:
    - name: dest
      hostPath:
        path: $dest
        type: DirectoryOrCreate
YAML
"${kubectl[@]}" -n "$namespace" wait pod "$pod" --for=condition=Ready --timeout="${STAGE_TIMEOUT:-180s}" > /dev/null
"${kubectl[@]}" -n "$namespace" exec "$pod" -- find /stage -mindepth 1 -delete
"${kubectl[@]}" -n "$namespace" cp "$src/." "$pod:/stage"
"${kubectl[@]}" -n "$namespace" exec "$pod" -- sh -c "$finish" sh /stage
