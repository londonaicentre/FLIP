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
# Move the release's Deployments onto Recreate before Helm upgrades them (FLIP#1524).
#
# Helm 4 applies the release server-side. A Deployment created as RollingUpdate carries the
# rollingUpdate defaults the API server filled in, which no field manager owns, so the apply
# keeps them; when the chart now renders that Deployment as Recreate the result is invalid
# ("spec.strategy.rollingUpdate: Forbidden") and the upgrade stops. FLIP#857 did this to
# data-access-api. Switch each such Deployment first, the way `kubectl patch` would by hand;
# Helm then finds the strategy it wants already in place. Helm 3 applies client-side and never
# hit this; the patch does no harm there.
#
# Usage: helm template ... | NAMESPACE=<ns> RELEASE_NAME=<release> [KUBECTL="kubectl --context <ctx>"] migrate-strategy.sh
set -euo pipefail

KUBECTL=${KUBECTL:-kubectl}

# The rendered Deployments the chart wants on Recreate. kubectl parses the render, so this needs no YAML library.
recreate=$($KUBECTL create --dry-run=client -f - -o jsonpath='{.kind}/{.metadata.name} {.spec.strategy.type}{"\n"}' |
    awk '$1 ~ /^Deployment\// && $2 == "Recreate" { print substr($1, 12) }')

live=$($KUBECTL -n "$NAMESPACE" get deployment -l "app.kubernetes.io/instance=$RELEASE_NAME" \
    -o jsonpath='{range .items[*]}{.metadata.name} {.spec.strategy.type}{"\n"}{end}')

while read -r name strategy; do
    [ "$strategy" = RollingUpdate ] && grep -qxF "$name" <<<"$recreate" || continue
    echo "🔧 $name: RollingUpdate -> Recreate before the upgrade (FLIP#1524)"
    $KUBECTL -n "$NAMESPACE" patch deployment "$name" --type=json \
        -p='[{"op":"remove","path":"/spec/strategy/rollingUpdate"},{"op":"replace","path":"/spec/strategy/type","value":"Recreate"}]'
done <<<"$live"
