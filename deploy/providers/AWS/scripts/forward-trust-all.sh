#!/bin/bash
# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
#
# forward-trust-all.sh
#
# Opens SSM port forwards for all Trust web UIs and API swagger docs in parallel.
# Prints the URLs to paste into a browser. Ctrl+C stops all forwards.
#
# Requires: aws CLI with a valid session, and the AWS SSM Session Manager plugin.

set -uo pipefail

INSTANCE_ID=$(terraform output -raw TrustEc2InstanceId 2>&1) || {
  echo "❌ terraform output -raw TrustEc2InstanceId failed:"
  echo "$INSTANCE_ID"
  echo "   Did you run 'terraform apply' from deploy/providers/AWS?"
  exit 1
}
if [ -z "$INSTANCE_ID" ] || [ "$INSTANCE_ID" = "null" ]; then
  echo "❌ TrustEc2InstanceId is empty/null — Trust EC2 not deployed?"
  exit 1
fi

LOG_DIR=$(mktemp -d -t ssm-fwd-XXXXXX)
declare -a PIDS=()
declare -a NAMES=()
declare -a LOGS=()

forward() {
  local remote=$1
  local local_port=$2
  local name=$3
  local url_hint=$4
  local log="$LOG_DIR/${name}.log"
  aws ssm start-session --target "$INSTANCE_ID" \
    --document-name AWS-StartPortForwardingSession \
    --parameters "portNumber=${remote},localPortNumber=${local_port}" \
    >"$log" 2>&1 &
  PIDS+=($!)
  NAMES+=("$name")
  LOGS+=("$log")
  printf "  %-18s %s\n" "$name" "$url_hint"
}

cleanup() {
  echo ""
  echo "🛑 Stopping all port forwards..."
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  rm -rf "$LOG_DIR"
  exit 0
}
trap cleanup EXIT INT TERM

echo "🔀 Opening SSM port forwards to Trust EC2 ($INSTANCE_ID)..."
echo ""
forward 8104 8104 "XNAT"            "http://localhost:8104"
forward 8042 8042 "Orthanc"         "http://localhost:8042"
forward 8020 8020 "trust-api"       "http://localhost:8020/docs"
forward 8001 8001 "imaging-api"     "http://localhost:8001/docs"
forward 8010 8010 "data-access-api" "http://localhost:8010/docs"
forward 3000 3000 "Grafana"         "http://localhost:3000"
echo ""
echo "⏳ Sessions starting up (takes ~5s)..."
sleep 5

# Verify each session started successfully
failed=0
for i in "${!PIDS[@]}"; do
  if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
    echo "❌ ${NAMES[$i]} session failed — log:"
    cat "${LOGS[$i]}"
    failed=1
  fi
done

if [ $failed -eq 1 ]; then
  echo ""
  echo "One or more forwards failed. Common causes:"
  echo "  - SSM Session Manager plugin not installed (see README)"
  echo "  - AWS SSO session expired (run: aws sso login)"
  echo "  - Local port already in use"
  exit 1
fi

echo "✅ All forwards ready. Ctrl+C to stop."
wait
