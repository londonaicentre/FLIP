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

# Shared helpers for turning `nvflare provision` output into FLIP's services/ layout.
#
# Sourced by both provision-network.sh (full network) and add-client.sh (single
# incremental client) so the restructure logic lives in one place. This file only
# DEFINES functions — it runs nothing at top level — so the caller stays in control
# of `set -euo pipefail` and argument parsing.
#
# Contract (globals the caller must set before calling restructure_participant):
#   PROD_DIR      absolute/relative path to the `prod_NN` dir nvflare provision wrote
#   SERVICES_DIR  destination services/ dir the kits are restructured into
#   VERBOSE       "true" to emit vlog lines (any other value silences them)
# The *_template helpers leave ${LOG_LEVEL} / ${NUM_AVAILABLE_GPUS} /
# ${MEMORY_PER_GPU_IN_GIB} as literal placeholders — they are expanded later at
# container start, not here.

log() { echo "$*"; }
vlog() { if [[ "${VERBOSE:-false}" == "true" ]]; then echo "   [verbose] $*"; fi }

# Get the first participant name of a given type from the project YAML.
# This avoids hardcoding participant (server, admin) names in the script.
# Selects the first match inside yq instead of piping to `head -n 1`, so it stays
# pipefail-safe: a multi-match YAML can't SIGPIPE yq into a cryptic 141. Emits an
# empty string when no participant of the type exists, so callers guard on `-z`.
get_participant_name_by_type() {
  local project_yaml="$1"
  local participant_type="$2"

  yq -r \
    "[.participants[] | select(.type == \"${participant_type}\")][0].name // \"\"" \
    "$project_yaml"
}

# Like get_participant_name_by_type but returns every matching name (one per
# line), not just the first. Used to restructure all clients regardless of how
# many the project YAML declares — the generator may have expanded it to N (see
# generate-project-yaml.sh).
get_participant_names_by_type() {
  local project_yaml="$1"
  local participant_type="$2"

  yq -r \
    ".participants[] | select(.type == \"${participant_type}\") | .name" \
    "$project_yaml"
}

# Restructure a single participant's kit from PROD_DIR into SERVICES_DIR.
restructure_participant() {
    local participant_name="$1"
    local participant_name_dest="$2"
    local is_client="$3"

    local src_path="${PROD_DIR}/${participant_name}"
    local dest_path="${SERVICES_DIR}/${participant_name_dest}"

    # Fail fast if `nvflare provision` did not produce this participant's kit.
    # Without this, the restructure below would silently mkdir an empty dest and
    # report success, masking a malformed project YAML or a partial provision.
    if [[ ! -d "${src_path}" ]]; then
        echo "Error: provisioned participant directory not found: ${src_path}" >&2
        exit 1
    fi

    echo " - Restructuring ${participant_name}"

    mkdir -p "${dest_path}"

    # Move standard directories
    for dir in startup local transfer; do
        if [[ -d "${src_path}/${dir}" ]]; then
            vlog "Moving '${src_path}/${dir}' to '${dest_path}/'"
            mv "${src_path}/${dir}" "${dest_path}/"
        fi
    done

    # Move readme.txt
    if [[ -f "${src_path}/readme.txt" ]]; then
        vlog "Moving 'readme.txt' to '${dest_path}/'"
        mv "${src_path}/readme.txt" "${dest_path}/"
    fi

    # Fix start.sh to run in foreground (remove & from sub_start.sh call)
    local start_script="${dest_path}/startup/start.sh"
    if [[ -f "${start_script}" ]]; then
        vlog "Modifying start.sh script to run 'sub_start.sh' process in foreground (removing &)"
        sed -i 's|\$DIR/sub_start.sh &|\$DIR/sub_start.sh|g' "${start_script}"
    fi

    # Create log_config.template.json
    local local_dir="${dest_path}/local"
    if [[ -d "${local_dir}" ]]; then
        create_log_config_template "${local_dir}"
    fi

    # Create resources template configs (FL clients only)
    if [[ -d "${local_dir}" && "${is_client}" == "1" ]]; then
        create_resources_template "${local_dir}"
    fi
}

# Create log_config.template.json from log_config.json.default
create_log_config_template() {
    local local_dir="$1"
    local default_file="log_config.json.default"
    local template_file="log_config.template.json"
    local src="${local_dir}/${default_file}"
    local dest="${local_dir}/${template_file}"

    if [[ ! -f "${src}" ]]; then
        vlog "File ${src} not found, skipping"
        return
    fi
    vlog "Creating ${template_file}"

    # Replace "level": "INFO" with "level": "${LOG_LEVEL}"
    sed 's|"level"[[:space:]]*:[[:space:]]*"INFO"|"level": "${LOG_LEVEL}"|g' \
        "${src}" > "${dest}"
}

# Create resources.template.json from resources.json.default (FL clients only)
create_resources_template() {
    local local_dir="$1"
    local default_file="resources.json.default"
    local template_file="resources.template.json"
    local src="${local_dir}/${default_file}"
    local dest="${local_dir}/${template_file}"

    if [[ ! -f "${src}" ]]; then
        vlog "File ${src} not found, skipping"
        return
    fi
    vlog "Creating ${template_file}"

    # Replace num_of_gpus and mem_per_gpu_in_GiB with template variables
    sed -E '
      s|"num_of_gpus"[[:space:]]*:[[:space:]]*[0-9]+|"num_of_gpus": ${NUM_AVAILABLE_GPUS}|g;
      s|"mem_per_gpu_in_GiB"[[:space:]]*:[[:space:]]*[0-9]+|"mem_per_gpu_in_GiB": ${MEMORY_PER_GPU_IN_GIB}|g
    ' "${src}" > "${dest}"
}
