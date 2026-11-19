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
#   - a kind node is a Docker container of the same name: `docker exec` / `docker cp`;
#   - any other node (k3s, a VM, bare metal): a short-lived root pod pinned to the node mounts <dest> as a hostPath,
#     and the folder goes in with `kubectl cp`. That needs no docker or shell on the host, but RBAC to read nodes and
#     to create and exec into a pod in $NAMESPACE, which is as good as root on that node. If the cluster will not run
#     the pod, the script prints the same steps as one command to run as root on the node.
#
# The same steps on both paths: copy <src> into a staging folder inside <dest> (made 0700 first with --owner), then
# swap it in for the previous contents, create the --subdirs (kubelet would make a missing subPath root 0700) and set
# the permissions: --owner <uid> is chmod -R go-rwx + chown -R uid:uid, --world-readable is chmod -R a+rX. A failed
# copy leaves the previous contents in place.
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
# <dest> is emptied as root, so refuse anything that could name / or a top-level folder.
dest="${dest%/}"
case "$dest/" in
  /*/*/) case "$dest/" in *//*|*/./*|*/../*) dest_ok="" ;; *) dest_ok=1 ;; esac ;;
  *) dest_ok="" ;;
esac
[ -n "$dest_ok" ] || {
  echo "❌ --dest must be an absolute path at least two levels deep, without . or .. (it is emptied): '$dest'" >&2
  exit 2
}
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
  permissions_by_hand="chmod -R go-rwx $dest && chown -R $owner:$owner $dest"
else
  permissions="chmod -R a+rX \"\$1\""
  permissions_by_hand="chmod -R a+rX $dest"
fi
# Run on the node with $1 the destination and $2 the staging folder inside it: before and after the copy.
staging=".flip-staging-$$"
prepare="set -e; mkdir -p \"\$1/\$2\"${owner:+; chmod 700 \"\$1\"}"
finish="set -e; find \"\$1\" -mindepth 1 -maxdepth 1 ! -name \"\$2\" -exec rm -rf {} \\;"
finish+="; find \"\$1/\$2\" -mindepth 1 -maxdepth 1 -exec mv {} \"\$1/\" \\;; rmdir \"\$1/\$2\""
finish+="${subdirs:+; for d in $subdirs; do mkdir -p \"\$1/\$d\"; done}; $permissions"

kind_cluster=""
if command -v "$docker" > /dev/null 2>&1; then
  kind_cluster=$("$docker" inspect -f '{{index .Config.Labels "io.x-k8s.kind.cluster"}}' "$node" 2> /dev/null || true)
fi

if [ -n "$kind_cluster" ]; then
  echo "📦 $src → kind node $node:$dest (docker cp)"
  "$docker" exec "$node" sh -c "$prepare" sh "$dest" "$staging"
  "$docker" cp "$src/." "$node:$dest/$staging/"
  "$docker" exec "$node" sh -c "$finish" sh "$dest" "$staging"
  exit 0
fi

# When the cluster will not run the pod, the same steps for the operator to run as root on the node.
by_hand() {
  echo "❌ $1. Stage it on $node as root instead, from a copy of $src on that host:" >&2
  echo "   sudo sh -c 'mkdir -p $dest && find $dest -mindepth 1 -delete && cp -a <your copy of $src>/. $dest/${subdirs:+ && for d in $subdirs; do mkdir -p $dest/\$d; done} && $permissions_by_hand'" >&2
  exit 1
}

pod="flip-stage-$(date +%s)-$$"
echo "📦 $src → node $node:$dest (pod $namespace/$pod, kubectl cp)"
# An account limited to the namespace cannot read it; the pod's apply below then names what is wrong.
"${kubectl[@]}" get namespace "$namespace" > /dev/null 2>&1 \
  || "${kubectl[@]}" create namespace "$namespace" > /dev/null 2>&1 || true
# sleep runs as PID 1, which has no SIGTERM handler, so the default 30 s grace period would only leave it Terminating.
remove_pod() {
  "${kubectl[@]}" -n "$namespace" delete pod "$pod" --ignore-not-found --wait=false --grace-period=1 > /dev/null 2>&1 \
    || echo "⚠️  could not remove the staging pod: kubectl --context $context -n $namespace delete pod $pod" >&2
}
trap remove_pod EXIT
if ! "${kubectl[@]}" -n "$namespace" apply -f - > /dev/null << YAML
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
      command: ["sleep", "14400"]  # outlives a slow vocabulary copy; remove_pod deletes it at once
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
then
  by_hand "the cluster would not run the staging pod (kubectl's reason is above)"
fi
"${kubectl[@]}" -n "$namespace" wait pod "$pod" --for=condition=Ready --timeout="${STAGE_TIMEOUT:-180s}" > /dev/null \
  || by_hand "the staging pod did not start within ${STAGE_TIMEOUT:-180s} (is $STAGE_IMAGE reachable from the node?)"
"${kubectl[@]}" -n "$namespace" exec "$pod" -- sh -c "$prepare" sh /stage "$staging"
"${kubectl[@]}" -n "$namespace" cp "$src/." "$pod:/stage/$staging"
"${kubectl[@]}" -n "$namespace" exec "$pod" -- sh -c "$finish" sh /stage "$staging"
