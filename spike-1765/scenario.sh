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
# Spike #1765: the whole scenario, start to finish, with timings. Logs to out/scenario.log.
set -uo pipefail
cd "$(dirname "$0")"
K="kubectl --context kind-flip-1765"
R=./rig.sh
now() { date +%s; }
say() { echo "[$(date +%H:%M:%S)] $*"; }
json() { python3 -c "import sys,json; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }
wait_online() { # <superlink> <node-id>
  for _ in $(seq 120); do
    $R flwr_cli "flwr supernode ls $1 --format json" | json "[n['status'] for n in d['nodes'] if n['node-id']=='$2'][0]" | grep -q online && return 0
    sleep 1
  done; return 1
}
run_app() { # <superlink> -> prints run id
  $R flwr_cli "flwr run . $1 --format json" | json "d['run-id']"
}
wait_run() { # <superlink> <run-id>
  for _ in $(seq 150); do
    s=$($R flwr_cli "flwr list $1 --run-id $2 --format json" | json "d['runs'][0]['status']")
    case $s in finished*) echo "$s"; return 0;; esac; sleep 2
  done; echo "$s"; return 1
}
spike_lines() { $R flwr_cli "flwr log $2 $1 --show" 2>&1 | grep SPIKE; }

say "1. hub: SuperLinks A and B, one fleet-proxy, operator"
$R base >/dev/null; $R gpus 1 >/dev/null; $R trust_rbac >/dev/null
t=$(now); $R superlink a >/dev/null; $R superlink b >/dev/null; $R proxy >/dev/null; $R operator >/dev/null
$K -n hub wait --for=condition=Ready pod --all --timeout=180s >/dev/null
for _ in $(seq 60); do $R flwr_cli "flwr supernode ls b --format json" 2>/dev/null | grep -q '"success": true' && break; sleep 1; done
say "   SuperLink B created -> Control API answering: $(( $(now)-t ))s"

say "2. hub registers each trust's per-project PUBLIC key on that project's SuperLink only"
A_ID=$($R register a trust1-a | json "d['node-id']"); B_ID=$($R register b trust1-b | json "d['node-id']")
say "   A: $A_ID   B: $B_ID"
say "   SuperLink A knows: $($R flwr_cli 'flwr supernode ls a --format json' | json "[n['node-id'] for n in d['nodes']]")"
say "   SuperLink B knows: $($R flwr_cli 'flwr supernode ls b --format json' | json "[n['node-id'] for n in d['nodes']]")"

say "3. trust-api creates both SuperNodes; the node has ONE (fake) GPU"
t=$(now); $R supernode a trust1-a >/dev/null; $R supernode b trust1-b >/dev/null
wait_online a "$A_ID" && say "   SuperNode A created -> online on SuperLink A: $(( $(now)-t ))s"
sleep 5; say "   pods: $($K -n trust get pods --no-headers -o custom-columns=N:.metadata.name,S:.status.phase | tr -s ' ' | paste -sd ';' -)"

say "4. project A runs while B waits for a GPU"
t=$(now); RA=$(run_app a); s=$(wait_run a "$RA"); say "   run $RA on A: $s in $(( $(now)-t ))s"; spike_lines a "$RA"

say "5. a second GPU appears: B is scheduled; A and B run at the same time"
t=$(now); $R gpus 2 >/dev/null; wait_online b "$B_ID" && say "   SuperNode B scheduled -> online: $(( $(now)-t ))s"
t=$(now); RA=$(run_app a); RB=$(run_app b)
sa=$(wait_run a "$RA"); sb=$(wait_run b "$RB"); say "   concurrent: A $sa, B $sb, both within $(( $(now)-t ))s"
spike_lines a "$RA"; spike_lines b "$RB"
say "   ClientApp B's view of the pod spec env (key must come from a Secret, not a literal):"
$K -n trust get deploy supernode-trust1-b -o jsonpath='{.spec.template.spec.containers[0].env[1]}'; echo

say "6. refusal: B's key dialling project A's host"
$R gpus 3 >/dev/null  # a free GPU, so the refusal is Flower's and not the scheduler's
$R supernode a trust1-b a rogue >/dev/null; sleep 25
say "   rogue pod: $($K -n trust get pods -l app=supernode-rogue --no-headers -o custom-columns=S:.status.phase,R:.status.containerStatuses[0].restartCount)"
$K -n trust logs deploy/supernode-rogue --tail=60 2>&1 | grep -E "Exit Code|code: 2004" | head -2
$K -n hub logs deploy/superlink-a --since=30s | grep -o "ApiError:2004.*exception: [^.]*" | head -1 | sed 's/public key: b.*exception/public key: <B key>, exception/' 
say "   SuperLink A still knows only: $($R flwr_cli 'flwr supernode ls a --format json' | json "[(n['node-id'],n['status']) for n in d['nodes']]")"
$K -n hub logs deploy/fleet-proxy --since=30s | grep activate-node | tail -1

say "7. teardown of project B"
t=$(now); AS="--as=system:serviceaccount:trust:trust-api"
$R flwr_cli "flwr supernode unregister $B_ID b --format json" | json "d['success']"
$K $AS -n trust delete deploy supernode-trust1-b supernode-rogue --wait=true >/dev/null
$K $AS -n trust delete secret supernode-trust1-b >/dev/null; $K $AS -n trust delete configmap project-b >/dev/null
$K -n hub delete deploy superlink-b --wait=true >/dev/null
$K -n hub delete svc superlink-b >/dev/null; $K -n hub delete secret superlink-b-tls >/dev/null
$K -n trust wait --for=delete pod -l app=supernode-trust1-b --timeout=60s >/dev/null 2>&1
say "   torn down in $(( $(now)-t ))s"
say "   left in trust: $($K -n trust get deploy,secret,configmap,pod --no-headers -o name | paste -sd ' ' -)"
say "   left in hub:   $($K -n hub get deploy,svc,secret --no-headers -o name | paste -sd ' ' -)"
say "   project A still works:"
RA=$(run_app a); say "   run $RA on A: $(wait_run a "$RA")"; spike_lines a "$RA"
