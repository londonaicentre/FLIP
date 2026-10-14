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
# SCRATCH (evidence for FLIP#1467, never merged). Times a real first install of xnat-web under the
# chart's probes: when `/` and /xapi/siteConfig/buildInfo first answer (asked the way kubelet asks,
# from inside the pod, no credentials), when the pod goes Ready, and that it then survives the
# liveness window with no restart.
set -uo pipefail

NS=flip-trust
SEL=app.kubernetes.io/component=xnat-web
start=$(date +%s)
since() { echo "+$(($(date +%s) - start))s"; }

POD=""
for _ in $(seq 1 300); do
  POD=$(kubectl get pod -n "$NS" -l "$SEL" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  [ -n "$POD" ] && break
  sleep 2
done
[ -n "$POD" ] || { echo "no xnat-web pod appeared"; exit 1; }
echo "$(since) pod ${POD}"
kubectl get pod -n "$NS" "$POD" \
  -o jsonpath='{range .spec.containers[?(@.name=="tomcat")]}liveness: {.livenessProbe}{"\n"}readiness: {.readinessProbe}{"\n"}{end}'

# shellcheck disable=SC2016 # expanded inside the pod
IN_POD='
ip=$(hostname -i | awk "{print \$1}")
get()  { curl -s -o /dev/null    -A kube-probe/1.33 -H "Accept: */*" -H "Connection: close" --max-time 5 -w "%{http_code}" "http://$ip:8080$1" 2>/dev/null || true; }
getL() { curl -s -o /dev/null -L -A kube-probe/1.33 -H "Accept: */*" -H "Connection: close" --max-time 5 -w "%{http_code}" "http://$ip:8080$1" 2>/dev/null || true; }
echo "/=$(get /) /(followed)=$(getL /) buildInfo=$(get /xapi/siteConfig/buildInfo) initialized=$(get /xapi/siteConfig/initialized)"
'

last="" ready=""
deadline=$((start + 1500))
while [ "$(date +%s)" -lt "$deadline" ]; do
  running=$(kubectl get pod -n "$NS" "$POD" \
    -o jsonpath='{.status.containerStatuses[?(@.name=="tomcat")].state.running.startedAt}' 2>/dev/null || true)
  if [ -n "$running" ]; then
    codes=$(kubectl exec -n "$NS" "$POD" -c tomcat -- sh -c "$IN_POD" 2>/dev/null || echo "exec failed")
    if [ "$codes" != "$last" ]; then
      echo "$(since) $(date -u +%H:%M:%S) ${codes}"
      last=$codes
    fi
  fi
  ready=$(kubectl get pod -n "$NS" "$POD" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)
  if [ "$ready" = "True" ]; then
    echo "$(since) $(date -u +%H:%M:%S) pod Ready"
    break
  fi
  sleep 2
done
[ "$ready" = "True" ] || { echo "xnat-web never went Ready"; kubectl describe pod -n "$NS" "$POD"; exit 1; }

# Stay past the whole liveness window (initialDelaySeconds 120 + 3 failures x 20 s) before judging.
echo "waiting out the liveness window..."
for _ in $(seq 1 120); do
  up=$(kubectl get pod -n "$NS" "$POD" -o json | python3 -c '
import json, sys, datetime
p = json.load(sys.stdin)
c = next(s for s in p["status"]["containerStatuses"] if s["name"] == "tomcat")
t = c["state"].get("running", {}).get("startedAt")
print(int((datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(t.replace("Z", "+00:00"))).total_seconds()) if t else -1)')
  [ "$up" -ge 300 ] && break
  sleep 5
done

kubectl get pod -n "$NS" "$POD" -o json | python3 -c '
import json, sys, datetime
p = json.load(sys.stdin)
ts = lambda s: datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
created = ts(p["metadata"]["creationTimestamp"])
cond = {c["type"]: c for c in p["status"]["conditions"]}
tomcat = next(s for s in p["status"]["containerStatuses"] if s["name"] == "tomcat")
started = ts(tomcat["state"]["running"]["startedAt"])
ready = ts(cond["Ready"]["lastTransitionTime"])
init = ts(cond["Initialized"]["lastTransitionTime"])
print(f"pod created            {created:%H:%M:%S}")
print(f"init containers done   {init:%H:%M:%S}  (+{(init - created).seconds}s)")
print(f"tomcat started         {started:%H:%M:%S}  (+{(started - created).seconds}s)")
print(f"Ready                  {ready:%H:%M:%S}  (+{(ready - created).seconds}s after creation, +{(ready - started).seconds}s after tomcat started)")
ready_now = cond["Ready"]["status"]
restarts = tomcat["restartCount"]
print(f"Ready now              {ready_now}")
print(f"tomcat restartCount    {restarts}")
'
echo "--- events for ${POD}"
kubectl get events -n "$NS" --field-selector "involvedObject.name=${POD}" --sort-by=.lastTimestamp \
  -o custom-columns=LAST:.lastTimestamp,COUNT:.count,TYPE:.type,REASON:.reason,MESSAGE:.message
echo "--- probe failures (Unhealthy events)"
kubectl get events -n "$NS" --field-selector "involvedObject.name=${POD},reason=Unhealthy" \
  -o custom-columns=COUNT:.count,MESSAGE:.message || true
kubectl get pods -n "$NS" -o wide
