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


# Make scripts executable
chmod +x /app/startup/start.sh
chmod +x /app/startup/sub_start.sh
chmod +x /app/startup/stop_fl.sh

#################################################################
###### GPU config ###############################################
#################################################################
# Default values (override via env vars)
export NUM_AVAILABLE_GPUS=${NUM_AVAILABLE_GPUS:-1}
export MEMORY_PER_GPU_IN_GIB=${MEMORY_PER_GPU_IN_GIB:-16}

CFG_DIR="/app/local"
TEMPLATE="$CFG_DIR/resources.template.json"
CFG_PATH="$CFG_DIR/resources.json"

# Use envsubst to replace environment variables in the template
echo "🔧 Generating resources.json from template..."
envsubst < "$TEMPLATE" > "$CFG_PATH"
echo "✅ resources.json written to ${CFG_PATH}"
#################################################################

#################################################################
###### Log config ###############################################
#################################################################
# Define default log level if not set
export LOG_LEVEL=${LOG_LEVEL:-INFO}

LOG_TEMPLATE="/app/local/log_config.template.json"
LOG_CFG="/app/local/log_config.json"

# Use envsubst to replace environment variables in the template
echo "🔧 Generating log_config.json from template..."
envsubst < "$LOG_TEMPLATE" > "$LOG_CFG"
echo "✅ log_config.json written to ${LOG_CFG}"
#################################################################

# Cleanup any existing processes
echo "[entrypoint] Cleaning up stale processes and files..."
pkill -f "nvflare" || true
rm -f /app/pid.fl /app/daemon_pid.fl

# Start FL client
echo "[entrypoint] Starting FL client with:"
echo "  NUM_AVAILABLE_GPUS=${NUM_AVAILABLE_GPUS}"
echo "  MEMORY_PER_GPU_IN_GIB=${MEMORY_PER_GPU_IN_GIB}"
echo "  LOG_LEVEL=${LOG_LEVEL}"
cd /app/startup

# Call the startup script with the provided parameters
./start.sh

# Handle graceful shutdown
trap "echo '[entrypoint] Caught SIGTERM, stopping ...'; /app/startup/stop_fl.sh && rm -f /app/pid.fl /app/daemon_pid.fl" SIGTERM

# Wait for background processes
wait
