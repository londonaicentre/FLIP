#!/bin/bash
#
# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

# Optional: Activate virtual env if needed
source /app/.venv/bin/activate

# Clean up stale daemon PID before starting
rm -f /app/pid.fl /app/daemon_pid.fl

# Start FL server
echo "[entrypoint] Starting FL server with:"
echo "  LOG_LEVEL=${LOG_LEVEL}"
cd /app/startup

# Call the startup script with the provided parameters
./start.sh

# Handle graceful shutdown
trap "echo '[entrypoint] Caught SIGTERM, stopping ...'; /app/startup/stop_fl.sh && rm -f /app/pid.fl /app/daemon_pid.fl" SIGTERM

# Wait for background processes
wait
