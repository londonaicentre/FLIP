#!/bin/bash
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

# Exit on first error, undefined variable, or pipe failure.
set -euo pipefail

# Configures the XNAT Container Service and the dcm2niix command, on both deployment shapes: the
# Compose trust runs it inside xnat-web (`make -C trust/xnat xnat-configure`), and the Helm chart's
# xnat-init Job runs this same file from the same image (its configure-dcm2niix container), as it
# does configure-xnat.sh since FLIP#993. Run after configure-xnat.sh, which sets the admin password
# and grants the admin the ContainerManager role. One copy, so the fixes below reach both (FLIP#1451).
#
# Environment:
#   XNAT_ADMIN_USER      - XNAT admin username
#   XNAT_ADMIN_PASSWORD  - XNAT admin password (must match what configure-xnat.sh set)
#   XNAT_URL             - XNAT's internal URL; default http://xnat-web:8080 (both shapes)
#   XNAT_CS_BACKEND      - docker (default: Compose, Docker Swarm through xnat-socket-proxy) or
#                          kubernetes (the chart: the Container Service's Kubernetes backend)
#   DATA_PATH            - docker only: host path for the Docker path translation
#   XNAT_CS_PVC_NAME     - kubernetes only: the xnat-web data PVC the conversion Jobs mount
#

XNAT_URL="${XNAT_URL:-http://xnat-web:8080}"
XNAT_CS_BACKEND="${XNAT_CS_BACKEND:-docker}"
DCM2NIIX_NAME="dcm2niix"
# imaging-api names every DICOM->NIfTI subscription it creates this
# (imaging_api/services/projects.py, DCM2NIIX_SUBSCRIPTION_NAME), which is how the repair below
# tells FLIP's subscriptions from anyone else's.
DCM2NIIX_SUBSCRIPTION_NAME="DICOM-NIfTI Conversion"

case "$XNAT_CS_BACKEND" in
  docker) backend_setting=DATA_PATH ;;
  kubernetes) backend_setting=XNAT_CS_PVC_NAME ;;
  *)
    echo "ERROR: XNAT_CS_BACKEND must be docker or kubernetes, not '$XNAT_CS_BACKEND'" >&2
    exit 1
    ;;
esac
# An explicit check, not ${VAR:?}: under the EXIT trap below, bash 3.2 exits 0 after an expansion
# error, which would report a refused run as a success.
if [[ -z "${!backend_setting:-}" ]]; then
  echo "ERROR: $backend_setting is required for the $XNAT_CS_BACKEND backend" >&2
  exit 1
fi

# Scratch files for this run, removed however it ends. XNAT's listings go through files rather than
# command-line arguments: Linux caps a single argument at 128 KiB (MAX_ARG_STRLEN), which a
# subscription or project listing passes at a few hundred entries, and jq then dies with "Argument
# list too long" on every deploy.
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

# Wait for XNAT to be available (wall-clock bounded, and each probe carries
# its own timeout, so a dead or wedged XNAT fails the deploy loudly instead
# of printing dots forever).
echo "Waiting for XNAT to be available..."
wait_start=$SECONDS
deadline=$((wait_start + 900))
until curl --output /dev/null --silent --head --fail \
  --connect-timeout 5 --max-time 10 "$XNAT_URL/app/template/Login.vm"; do
  if [[ "$SECONDS" -ge "$deadline" ]]; then
    echo "ERROR: XNAT did not become available within $((SECONDS - wait_start))s" >&2
    exit 1
  fi
  printf '.'
  sleep 1
done
echo "XNAT is up!"

# Helper: curl wrapper for XNAT's REST API. On 2xx, emits the response body
# on stdout for the caller to capture. On any other status — or a curl
# transport failure — reports the request (and, for HTTP failures, the
# response body) on stderr and returns non-zero, which the script's
# `set -e` turns into an abort at the call site (so don't call this inside
# `if`/`||` without handling the failure). Use it for every request to
# XNAT's REST API, reads included: an unnoticed bad GET response is how the
# original empty-CMD_ID deploy failure slipped through — bare `curl -s`
# surfaced neither HTTP errors nor unexpected bodies. The credentials are added
# after "$@", so the logged arguments never carry them.
xnat_curl() {
  local response
  local status
  local body
  local curl_exit=0
  response=$(curl -sS --connect-timeout 10 --max-time 120 -w '\n%{http_code}' "$@" \
    -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}") || curl_exit=$?
  if [[ "$curl_exit" -ne 0 ]]; then
    echo "ERROR: curl transport failure (exit $curl_exit)" >&2
    echo "  args: $*" >&2
    return 1
  fi
  status=$(printf '%s' "$response" | tail -n1)
  body=$(printf '%s' "$response" | sed '$d')
  # Strip CRs so a middlebox emitting \r\n line endings can't make the
  # callers' numeric/boolean guards fail on an invisible character (a raw
  # CR is not valid inside JSON strings, so this is lossless).
  status=${status//$'\r'/}
  body=${body//$'\r'/}
  # Fail closed: anything other than a literal 2xx status line (including
  # an empty or non-numeric one) is an error.
  if ! [[ "$status" =~ ^2[0-9]{2}$ ]]; then
    echo "ERROR: XNAT request failed with HTTP $status" >&2
    echo "  args: $*" >&2
    echo "  body: $body" >&2
    return 1
  fi
  printf '%s' "$body"
}

# Container Service >= 3.7.0 answers every call below with 401/403 unless the admin holds the
# ContainerManager role, which configure-xnat.sh grants. Name that cause rather than let the first
# call fail with a bare status (TROUBLESHOOTING 2.3a).
ADMIN_ROLES=$(xnat_curl "$XNAT_URL/xapi/users/${XNAT_ADMIN_USER}/roles/")
if ! jq -e 'index("ContainerManager")' >/dev/null <<<"$ADMIN_ROLES"; then
  echo "ERROR: ${XNAT_ADMIN_USER} does not hold the ContainerManager role, so every Container Service call would be refused." >&2
  echo "  roles: $ADMIN_ROLES (configure-xnat.sh grants it; see TROUBLESHOOTING 2.3a)" >&2
  exit 1
fi

# ----------------------------------------------------------------
# CONTAINER SERVICE BACKEND
# ----------------------------------------------------------------

# docker: the Container Service reaches Docker through the xnat-socket-proxy sidecar (see
# docker-compose-stack.yml), not a socket mounted into xnat-web, and translates XNAT's paths to the
# host's DATA_PATH. kubernetes: the Container Service's own Kubernetes backend. Its entry carries
# the PVC mount settings (FLIP#565): without them the plugin bind-mounts the scan path as a node
# hostPath, which only exists inside the xnat-web pod, and dcm2niix finds no DICOM in /input.
# combined-pvc-name is the single xnat-web data PVC (its root holds both archive/ and build/), and
# combined-path-translation must keep its trailing slash, or the Job's subPath keeps a leading `/`
# and the Kubernetes API rejects it with HTTP 422 (TROUBLESHOOTING 2.3b).
case "$XNAT_CS_BACKEND" in
  docker)
    echo "Path translation with DATA_PATH=$DATA_PATH"
    backend_config=$(jq --arg data_path "$DATA_PATH" \
      '.["path-translation-docker-prefix"] = $data_path' container-service-backend-configuration.json)
    backend_label="Docker (via xnat-socket-proxy)"
    ;;
  kubernetes)
    echo "Kubernetes backend, conversion Jobs mounting PVC $XNAT_CS_PVC_NAME"
    backend_config=$(jq --arg pvc "$XNAT_CS_PVC_NAME" \
      '.["combined-pvc-name"] = $pvc' container-service-backend-configuration.kubernetes.json)
    backend_label="the Kubernetes API"
    ;;
esac

echo "backend_config: $backend_config"
# curl exits 0 on HTTP 4xx/5xx (no --fail), so check the status explicitly — if XNAT
# rejects the new backend config, the ping below could still pass against a previously
# registered backend and mask the failure. "000" = transport error / timeout.
post_status=$(curl -s -o /tmp/docker-server-post.json --max-time 60 -w "%{http_code}" \
  -X POST "$XNAT_URL/xapi/docker/server" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "$backend_config") || post_status="000"
if [[ "$post_status" != 2* ]]; then
  echo "ERROR: POST /xapi/docker/server returned HTTP $post_status"
  cat /tmp/docker-server-post.json 2>/dev/null || true
  exit 1
fi

# Fail loudly here if the backend cannot be reached — the command registration below would still
# "succeed", but every dcm2niix launch would then fail at conversion time. The ping is the same
# endpoint on both backends; on Kubernetes it lists pods in XNAT's namespace (container-service
# 3.8.1 KubernetesClientImpl.java:192-206), which xnat-rbac.yaml grants.
echo "Pinging $backend_label..."
ping_status=$(curl -s -o /dev/null --max-time 60 -w "%{http_code}" "$XNAT_URL/xapi/docker/server/ping" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}") || ping_status="000"
if [[ "$ping_status" != "200" ]]; then
  echo "ERROR: Container Service cannot reach $backend_label (HTTP $ping_status)"
  exit 1
fi
echo "Container Service -> $backend_label: OK"

# ----------------------------------------------------------------
# THE dcm2niix COMMAND BODY
# ----------------------------------------------------------------

# The command definition sent to XNAT, both to register it and to update it in place. One path,
# so every call below sends the same body.
DCM2NIIX_COMMAND_JSON="dcm2niix_command.json"
DCM2NIIX_IMAGE=$(jq -r '.image // empty' "$DCM2NIIX_COMMAND_JSON")
if [[ "$XNAT_CS_BACKEND" == kubernetes ]]; then
  # Registered without the command's resource settings (FLIP#1448). On this backend the Container
  # Service turns limit-cpu into a pod CPU *limit* and sets no CPU request (container-service 3.8.1
  # has no reserve-cpu), so Kubernetes defaults the request to the limit: every dcm2niix pod
  # reserved a whole CPU, plus 1000 MiB for reserve-memory, and on a 4-vCPU AKS node ~300 of them
  # filled the node's requests until nothing else could schedule. Without them each pod is
  # BestEffort: it reserves nothing and runs at the lowest CPU weight. The update below must send
  # this body too: it nulls any resource key the body leaves out (CommandEntity.java:116-118), so it
  # is also what moves an existing command off them. On Docker the same keys are container limits,
  # and the file goes unchanged.
  DCM2NIIX_COMMAND_JSON="$WORK_DIR/dcm2niix_command.json"
  jq 'del(."reserve-memory", ."limit-memory", ."limit-cpu")' dcm2niix_command.json >"$DCM2NIIX_COMMAND_JSON"
fi

# ----------------------------------------------------------------
# IMAGE HOST (registry entry for the pinned dcm2niix image)
# ----------------------------------------------------------------

# In swarm mode the Container Service resolves registry credentials for `docker service create` by
# looking up an image-host entry whose `url` equals the registry hostname parsed from the image
# string — the BARE hostname, no scheme (container-service 3.8.1, DockerControlApi.authConfig →
# dockerHubService.getByUrl). With no matching entry the resolved AuthConfig is null, and the swarm
# launch path passes it to docker-java's withAuthConfig unguarded (unlike the pull path, which
# null-checks), so every launch dies with `NullPointerException: authConfig was not specified`
# before a service exists — even for a public image that needs no credentials. Register the
# registry as a credential-less image host so the lookup resolves. The hostname is derived from the
# image pinned in dcm2niix_command.json so it can never drift from the image actually launched; an
# image with no registry component is a Docker Hub image, covered by the built-in default entry.
# The Kubernetes backend pulls through kubelet, not the Container Service's docker client, so the
# NPE cannot occur there and no entry is needed.
DCM2NIIX_REGISTRY=${DCM2NIIX_IMAGE%%/*}
needs_image_host=false
if [[ "$XNAT_CS_BACKEND" == docker ]] \
  && [[ "$DCM2NIIX_REGISTRY" == *.* || "$DCM2NIIX_REGISTRY" == *:* || "$DCM2NIIX_REGISTRY" == localhost ]]; then
  needs_image_host=true
fi
if [[ "$needs_image_host" == true ]]; then
  echo "Ensuring image host '$DCM2NIIX_REGISTRY' is registered..."
  HUB_ID=$(xnat_curl "$XNAT_URL/xapi/docker/hubs" \
    | jq -r --arg url "$DCM2NIIX_REGISTRY" 'map(select(.url == $url)) | .[0].id // empty')
  if [[ -z "$HUB_ID" ]]; then
    xnat_curl -X POST "$XNAT_URL/xapi/docker/hubs" \
      -H "Content-Type: application/json" \
      -d "{\"name\": \"$DCM2NIIX_REGISTRY\", \"url\": \"$DCM2NIIX_REGISTRY\"}" >/dev/null
    echo "Image host '$DCM2NIIX_REGISTRY' registered."
  else
    echo "Image host '$DCM2NIIX_REGISTRY' already registered (id=$HUB_ID)."
  fi
else
  echo "No image host needed (backend $XNAT_CS_BACKEND, registry '$DCM2NIIX_REGISTRY')."
fi

# ----------------------------------------------------------------
# THE dcm2niix COMMAND
# ----------------------------------------------------------------

dcm2niix_wrapper_name=$(jq -r '.xnat[0].name // empty' "$DCM2NIIX_COMMAND_JSON")
if [[ -z "$dcm2niix_wrapper_name" ]]; then
  echo "ERROR: no .xnat[0].name wrapper in $DCM2NIIX_COMMAND_JSON." >&2
  exit 1
fi

# Update an existing dcm2niix command in place; register one only when there is none (FLIP#1451).
#
# This used to DELETE the command and register it again, and XNAT gives a new registration new
# ids. Each project's DICOM->NIfTI trigger is an event subscription whose action-key names the
# dcm2niix *wrapper* by id (CommandActionProvider:<wrapper id>), and XNAT keys the wrapper's
# per-project enablement by that id too (container-service 3.8.1 CommandActionProvider.java:102,111
# and :476-489; ContainerConfigServiceImpl.java:74-143). So every configure left each existing
# project's subscription naming an id that no longer existed: XNAT reports it valid:false and a scan
# archived into the project starts no conversion. Only projects created afterwards worked.
#
# POST /xapi/commands/{id} ("Update a Command", CommandRestApi.java:143-150) takes the full command
# JSON, applies it to the command at the path id whatever the body's own id says, and answers 200
# with an empty body. It rewrites the stored command from the body, image included, so a pin bump
# still reaches the registered command (CommandEntity.java:99-130). Wrappers are matched by NAME:
# the existing dcm2niix-scan is updated in place and keeps its id when the body carries none
# (CommandEntity.java:201-222, CommandWrapperEntity.java:41-44). Keeping the wrapper id is what keeps
# the site and per-project enablement, and every project's subscription, pointing at something real.
echo "Checking if $DCM2NIIX_NAME command exists..."
EXISTING=$(xnat_curl "$XNAT_URL/xapi/commands?name=$DCM2NIIX_NAME")
EXISTING_COUNT=$(jq 'length' <<<"$EXISTING")
if [[ "$EXISTING_COUNT" -gt 1 ]]; then
  # FLIP never registers a second one. Updating either would leave the other for imaging-api's
  # image lookup to pick, so this needs a person to decide which one the projects' triggers use.
  echo "ERROR: $EXISTING_COUNT '$DCM2NIIX_NAME' commands are registered ($(jq -r '[.[] | "command \(.id) (wrapper \(.xnat[0].id // "none"))"] | join(", ")' <<<"$EXISTING")); expected at most one." >&2
  echo "  Compare those wrapper ids with the action-keys in GET /xapi/events/subscriptions, delete the command no subscription names, then re-run." >&2
  exit 1
fi
CMD_ID=$(jq -r '.[0].id // empty' <<<"$EXISTING")

if [[ -n "$CMD_ID" ]]; then
  echo "Found existing command ID: $CMD_ID. Updating it in place..."
  xnat_curl -X POST "$XNAT_URL/xapi/commands/$CMD_ID" \
    -H "Content-Type: application/json" \
    -d @"$DCM2NIIX_COMMAND_JSON" >/dev/null
  echo "Command updated."
else
  # POST /xapi/commands returns the new command's numeric id as the response body — a bare JSON
  # number, not the command object (container-service 3.8.1, pinned in trust/xnat/README.md's
  # plugin table: CommandRestApi.createCommand returns ResponseEntity<Long>, and the numeric-id
  # guard below passed live during the XNAT 1.10.0 upgrade smoke test).
  # Read the id straight from the POST response rather than re-GETting
  # /xapi/commands?name=...: on prod that GET came back with no usable
  # command even though the POST appeared to succeed (root cause
  # undiagnosed), which left CMD_ID unusable and broke the enable/validation
  # URLs downstream.
  echo "Command not found. Registering it..."
  CMD_ID=$(xnat_curl -X POST "$XNAT_URL/xapi/commands" \
    -H "Content-Type: application/json" \
    -d @"$DCM2NIIX_COMMAND_JSON")
fi

if ! [[ "$CMD_ID" =~ ^[0-9]+$ ]]; then
  echo "ERROR: XNAT did not give a numeric $DCM2NIIX_NAME command id." >&2
  echo "  body: $CMD_ID" >&2
  exit 1
fi

# Read the command back by id: the wrapper id is what project triggers name, and the image proves
# the update carried the pin. Neither POST returns them.
REGISTERED=$(xnat_curl "$XNAT_URL/xapi/commands/$CMD_ID")
WRAPPER_ID=$(jq -r --arg w "$dcm2niix_wrapper_name" \
  '[.xnat[]? | select(.name == $w) | .id][0] // empty' <<<"$REGISTERED")
if ! [[ "$WRAPPER_ID" =~ ^[0-9]+$ ]]; then
  echo "ERROR: command $CMD_ID has no '$dcm2niix_wrapper_name' wrapper with a numeric id." >&2
  echo "  body: $REGISTERED" >&2
  exit 1
fi
REGISTERED_IMAGE=$(jq -r '.image // empty' <<<"$REGISTERED")
if [[ "$REGISTERED_IMAGE" != "$DCM2NIIX_IMAGE" ]]; then
  echo "ERROR: command $CMD_ID runs '$REGISTERED_IMAGE', not the pinned '$DCM2NIIX_IMAGE'." >&2
  exit 1
fi

echo "Command ID: $CMD_ID"
echo "Wrapper Name: $dcm2niix_wrapper_name (id $WRAPPER_ID)"
echo "Image: $REGISTERED_IMAGE"

# Enable the dcm2niix command at the site level (makes it available for per-project use)
# See https://wiki.xnat.org/container-service/container-service-api for more details
echo "Enabling $DCM2NIIX_NAME command..."
xnat_curl -X PUT "$XNAT_URL/xapi/commands/$CMD_ID/wrappers/$dcm2niix_wrapper_name/enabled" >/dev/null

# Note: this only enables the command site-wide so it can be used per-project.

# ----------------------------------------------------------------
# EVENT SERVICE
# ----------------------------------------------------------------

# Enable Event Service (required for per-project event subscriptions to work)
echo "Enabling event service..."
xnat_curl -X PUT "$XNAT_URL/xapi/events/prefs" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' >/dev/null

# Note: We intentionally do NOT create a site-wide event subscription here.
# Per-project event subscriptions are created by the imaging-api during project creation,
# controlled by the dicom_to_nifti flag. This ensures dcm2niix only auto-triggers
# for projects that have opted in to DICOM-to-NIfTI conversion.

# Every subscription and every project XNAT holds, read once into files for the repair and the
# report below (never into a command-line argument: see WORK_DIR). A subscription has no top-level
# project field: its projects are event-filter.project-ids (xnat-web 1.10.0 Subscription.java:18-34,
# EventFilter.java:23). The subscription listing is null while the Event Service is off
# (EventServiceImpl.java:153-157), hence `// []`; it was switched on just above.
SUBS_FILE="$WORK_DIR/subscriptions.json"
PROJECTS_FILE="$WORK_DIR/projects.json"
xnat_curl "$XNAT_URL/xapi/events/subscriptions" >"$SUBS_FILE"
xnat_curl "$XNAT_URL/data/projects?format=json" >"$PROJECTS_FILE"

# Repair the project triggers an earlier configure left on a deleted dcm2niix wrapper (the
# delete-and-register above, before FLIP#1451): re-point each of FLIP's subscriptions (named
# $DCM2NIIX_SUBSCRIPTION_NAME) that names any other wrapper at the current one. Anyone else's
# subscription is left alone.
#
# Order matters. XNAT validates the subscription on update and refuses one whose action is not
# enabled for every project it filters on (EventServiceImpl.java:122-142 ->
# EventSubscriptionEntityServiceImpl.java:204-218 -> CommandActionProvider.isActionAvailable), and
# the project's old enablement belongs to the deleted wrapper id. So enable the current wrapper for
# the project first, the call imaging-api makes when it creates the project, then update.
#
# PUT /xapi/events/subscription/{id} (EventServiceRestApi.java:139-146) merges the body over the
# stored subscription (Subscription.java:139-150), but an absent act-as-event-user becomes false and
# absent attributes become {} (SubscriptionUpdate.java:46-48), so both are carried over with active.
# The event filter (the project and the DICOM payload filter) is untouched.
#
# A subscription whose project is no longer in XNAT's project listing is skipped: the enable call
# would fail for it, and it can never fire. Validity is recomputed on every read, so the repaired
# ones read valid:true straight away (EventSubscriptionEntityServiceImpl.java:430-438). A repair XNAT
# refuses (its owner account cannot be loaded, say) is a warning and the run goes on: one broken
# subscription must not fail every later deploy, and the invalid-trigger report below names it again.
#
# The action key is XNAT's, not built here: GET /xapi/events/allactions lists one action per command
# wrapper, its display-name the wrapper's name and its key "<provider class>:<wrapper id>"
# (EventServiceRestApi.java:383-387, CommandActionProvider.java:389-403, MultiActionProvider.java:14-32).
# Take the one entry for this wrapper's name and id; everything before the id is the provider, which
# says which subscriptions name a Container Service wrapper at all.
ALL_ACTIONS=$(xnat_curl "$XNAT_URL/xapi/events/allactions")
DCM2NIIX_ACTION=$(jq -r --arg w "$dcm2niix_wrapper_name" --arg id "$WRAPPER_ID" '
  [(. // [])[]
   | select(.["display-name"] == $w and ((.["action-key"] // "") | split(":") | last) == $id)
   | .["action-key"]]
  | if length == 1 then .[0] else empty end' <<<"$ALL_ACTIONS")
if [[ -z "$DCM2NIIX_ACTION" ]]; then
  echo "ERROR: XNAT lists no single event-service action for wrapper '$dcm2niix_wrapper_name' (id $WRAPPER_ID)." >&2
  echo "  body: $ALL_ACTIONS" >&2
  exit 1
fi
ACTION_PROVIDER_PREFIX="${DCM2NIIX_ACTION%:*}:"
echo "Current $DCM2NIIX_NAME action key: $DCM2NIIX_ACTION"
echo "Checking project DICOM->NIfTI triggers for a stale $DCM2NIIX_NAME wrapper id..."
# One line per stale subscription: the subscription, plus `missing`, its projects XNAT no longer has.
STALE_SUBS=$(jq -c --arg name "$DCM2NIIX_SUBSCRIPTION_NAME" --arg prefix "$ACTION_PROVIDER_PREFIX" \
  --arg key "$DCM2NIIX_ACTION" --slurpfile projects "$PROJECTS_FILE" '
  [($projects[0].ResultSet.Result // [])[].ID] as $known
  | (. // [])[]
  | select(.name == $name)
  | select((.["action-key"] // "") | startswith($prefix))
  | select(.["action-key"] != $key)
  | select((.["event-filter"]["project-ids"] // []) | length > 0)
  | . + {missing: [.["event-filter"]["project-ids"][] | select(. as $p | $known | index($p) | not)]}' <"$SUBS_FILE")
REPAIRED=0
REPAIR_FAILED=0
while IFS= read -r sub; do
  [[ -n "$sub" ]] || continue
  sub_id=$(jq -r '.id' <<<"$sub")
  old_wrapper_id=$(jq -r --arg prefix "$ACTION_PROVIDER_PREFIX" '.["action-key"] | ltrimstr($prefix)' <<<"$sub")
  missing=$(jq -r '.missing | join(" ")' <<<"$sub")
  if [[ -n "$missing" ]]; then
    echo "Skipped subscription $sub_id (wrapper $old_wrapper_id): its project no longer exists in XNAT: $missing"
    continue
  fi
  projects=$(jq -r '.["event-filter"]["project-ids"][]' <<<"$sub")
  # xnat_curl reports each refusal itself; inside `if` it returns instead of aborting the run.
  failed=""
  for project in $projects; do
    if ! xnat_curl -X PUT "$XNAT_URL/xapi/projects/$project/commands/$CMD_ID/wrappers/$dcm2niix_wrapper_name/enabled" >/dev/null; then
      failed="enabling wrapper $WRAPPER_ID for project $project"
      break
    fi
  done
  if [[ -z "$failed" ]]; then
    update=$(jq -c --arg key "$DCM2NIIX_ACTION" \
      '{"action-key": $key, "active": .active, "act-as-event-user": (.["act-as-event-user"] // false), "attributes": (.attributes // {})}' \
      <<<"$sub")
    if ! xnat_curl -X PUT "$XNAT_URL/xapi/events/subscription/$sub_id" \
      -H "Content-Type: application/json" \
      -d "$update" >/dev/null; then
      failed="updating subscription $sub_id"
    fi
  fi
  if [[ -n "$failed" ]]; then
    echo "WARNING: could not repair the DICOM->NIfTI trigger of project(s) $(tr '\n' ' ' <<<"$projects")(subscription $sub_id, wrapper $old_wrapper_id): $failed was refused; see the error above." >&2
    REPAIR_FAILED=$((REPAIR_FAILED + 1))
    continue
  fi
  for project in $projects; do
    echo "Repaired the DICOM->NIfTI trigger of project $project: subscription $sub_id now runs wrapper $WRAPPER_ID (was $old_wrapper_id)."
  done
  REPAIRED=$((REPAIRED + 1))
done <<<"$STALE_SUBS"
echo "Project triggers repaired: $REPAIRED; could not repair: $REPAIR_FAILED."

# Name each FLIP project that has no DICOM->NIfTI trigger at all: the legacy cleanup this script
# used to run deleted every one (see below), so a Compose site configured before FLIP#1451 has none. They are not recreated here, because
# imaging-api creates a trigger active or inactive from the project's dicom_to_nifti setting, which
# only the hub holds (it reaches the trust once, in the create task); a trigger made by this admin
# script would also run as the admin, not the service account. imaging-api's restore-nifti-triggers
# recreates them once the operator says which projects convert (trust/xnat/README.md). FLIP's XNAT
# projects carry their hub project id, a UUID, as secondary_ID (imaging_api to_create_project).
jq -r --slurpfile subs "$SUBS_FILE" --arg name "$DCM2NIIX_SUBSCRIPTION_NAME" '
  [($subs[0] // [])[] | select(.name == $name) | (.["event-filter"]["project-ids"] // [])[]] as $covered
  | (.ResultSet.Result // [])[]
  | select((.secondary_ID // "") | test("^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"))
  | select(.ID as $id | ($covered | index($id)) == null)
  | "WARNING: project \(.ID) (hub project \(.secondary_ID)) has no DICOM->NIfTI trigger, so its scans are not converted. Restore it with restore-nifti-triggers (trust/xnat/README.md)."' \
  <"$PROJECTS_FILE" >&2

# This script deliberately DELETES NO EVENT SUBSCRIPTIONS. Do not add a cleanup step here.
#
# It used to carry one, for the site-wide subscription versions before 88adb78b (2026-03-24) created.
# Nothing has created that since, and the chart never did, so the step had nothing legitimate left to
# find. Its selector, `.["project-id"] == null`, named a field no subscription has, so it matched,
# and deleted, every project's subscription on every configure (FLIP#1451). It is removed rather
# than narrowed: a cleanup for something nothing creates can only misfire. FLIP#945 removes it too.

# ----------------------------------------------------------------
# VALIDATION
# ----------------------------------------------------------------

# Every xnat_curl above aborts the script (via set -e) on a non-2xx
# response, so reaching this block means every write was accepted; these
# re-GETs check the resulting state actually persisted. The wrapper-enabled
# endpoint returns a bare JSON boolean (container-service 3.8.1 —
# CommandConfigurationRestApi.isConfigurationEnabled returns a bare Boolean in
# the 3.8.1 source; re-verified live during the XNAT 1.10.0 upgrade smoke test).
echo " "
echo "Validating dcm2niix setup..."
WRAPPER_ENABLED=$(xnat_curl "$XNAT_URL/xapi/commands/$CMD_ID/wrappers/$dcm2niix_wrapper_name/enabled")
if [ "$WRAPPER_ENABLED" != "true" ]; then
  echo "ERROR: dcm2niix wrapper is not enabled site-wide (expected true)" >&2
  echo "  body: $WRAPPER_ENABLED" >&2
  exit 1
fi

# Verify the image host for the pinned image's registry persisted (swarm launches NPE without it)
if [[ "$needs_image_host" == true ]]; then
  HUB_COUNT=$(xnat_curl "$XNAT_URL/xapi/docker/hubs" \
    | jq -r --arg url "$DCM2NIIX_REGISTRY" 'map(select(.url == $url)) | length')
  if [ "$HUB_COUNT" -lt 1 ]; then
    echo "ERROR: no image host registered with url '$DCM2NIIX_REGISTRY' (expected >= 1)" >&2
    exit 1
  fi
fi

# Verify event service is enabled
EVENT_PREFS=$(xnat_curl "$XNAT_URL/xapi/events/prefs")
EVENT_STATUS=$(echo "$EVENT_PREFS" | jq -r '.enabled // empty')
if [ "$EVENT_STATUS" != "true" ]; then
  echo "ERROR: Event service is not enabled (expected true)" >&2
  echo "  body: $EVENT_PREFS" >&2
  exit 1
fi

# Report any of FLIP's project triggers XNAT still calls invalid. A warning, not a failure: one can be
# invalid for a reason this script does not own (its owner account disabled, a skipped project), and
# failing would block every later deploy on it.
xnat_curl "$XNAT_URL/xapi/events/subscriptions" \
  | jq -r --arg name "$DCM2NIIX_SUBSCRIPTION_NAME" '(. // [])[]
    | select(.name == $name and .valid != true)
    | "WARNING: invalid DICOM->NIfTI trigger: subscription \(.id) (\(.["action-key"])) for project(s) \((.["event-filter"]["project-ids"] // []) | join(", ")): \(.["validation-message"] // "no message")"' >&2

echo " "
echo "✅ dcm2niix configuration complete and validated!"
