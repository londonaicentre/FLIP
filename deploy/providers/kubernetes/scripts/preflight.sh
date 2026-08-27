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

# Preflight checks for 'make -C deploy/providers/kubernetes up'.
# Called automatically by the 'deploy' / 'up' Makefile targets.
# All parameters are injected as environment variables by the Makefile.
#
# Exit codes:
#   0  — all required checks passed (warnings may be present)
#   1  — one or more required checks failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$CHART_DIR/../../.." && pwd)"

# ── Parameters (injected by Makefile) ────────────────────────────────────────
HELM_BIN="${HELM:-helm}"
NAMESPACE="${NAMESPACE:-flip-trust}"
RELEASE_NAME="${RELEASE_NAME:-trust-release}"
PROD="${PROD:-}"
KIT="${KIT:-}"
OVERRIDES_FILE="${OVERRIDES_FILE:-}"

# Resolve env suffix (same logic as the Makefile)
if   [ "$PROD" = "true" ]; then ENV_SUFFIX="production"
elif [ "$PROD" = "stag"  ]; then ENV_SUFFIX="stag"
else                              ENV_SUFFIX="development"
fi

# ── Colour support ─────────────────────────────────────────────────────────────
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
    BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; BOLD=''; NC=''
fi

FAILURES=0
WARNINGS=0

pass()    { printf "  ${GREEN}✔${NC}  %s\n"        "$*"; }
fail()    { printf "  ${RED}✖${NC}  %s\n"          "$*"; FAILURES=$((FAILURES + 1)); }
warn()    { printf "  ${YELLOW}⚠${NC}  %s\n"        "$*"; WARNINGS=$((WARNINGS + 1)); }
hint()    { printf "      ${BLUE}→${NC}  %s\n"     "$*"; }
section() { printf "\n${BOLD}%s${NC}\n" "$*"; }

# True if version string $1 (X.Y[.Z], optional leading 'v') >= $2 (same format)
version_ge() {
    local a="${1#v}" b="${2#v}"
    local a1 a2 b1 b2
    IFS='.' read -r a1 a2 _ <<< "$a"
    IFS='.' read -r b1 b2 _ <<< "$b"
    a1="${a1:-0}"; a2="${a2:-0}"; b1="${b1:-0}"; b2="${b2:-0}"
    (( a1 > b1 )) || { (( a1 == b1 )) && (( a2 >= b2 )); }
}

# Extract the first X.Y.Z version token from stdin
extract_semver() { grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1; }

# ── Header ─────────────────────────────────────────────────────────────────────
printf "\n${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}\n"
printf "${BOLD}║  FLIP Kubernetes Deployment — Preflight Checks               ║${NC}\n"
printf "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}\n"
printf "  PROD=%s  NAMESPACE=%s  RELEASE=%s\n" \
    "${PROD:-(unset → development)}" "$NAMESPACE" "$RELEASE_NAME"
[ -n "$KIT"            ] && printf "  KIT=%s  (env: %s)\n" "$KIT" "$ENV_SUFFIX"
[ -n "$OVERRIDES_FILE" ] && printf "  OVERRIDES_FILE=%s\n" "$OVERRIDES_FILE"

# ════════════════════════════════════════════════════════════════════════════════
section "1. Required tools"
# ════════════════════════════════════════════════════════════════════════════════

HELM_OK=false
if ! command -v "$HELM_BIN" >/dev/null 2>&1; then
    fail "helm not found in PATH"
    hint "macOS:  brew install helm"
    hint "Linux:  curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-4 | bash"
    hint "Docs:   https://helm.sh/docs/intro/install/"
else
    HELM_VER="$("$HELM_BIN" version 2>/dev/null | extract_semver)"
    if [ -z "$HELM_VER" ]; then
        warn "helm found but version could not be parsed"
        HELM_OK=true
    elif version_ge "$HELM_VER" "3.16"; then
        pass "helm ${HELM_VER}  (>= 3.16 required)"
        HELM_OK=true
    else
        fail "helm ${HELM_VER} is too old — 3.16+ required"
        hint "Upgrade: https://helm.sh/docs/intro/install/"
    fi
fi

KUBECTL_OK=false
if ! command -v kubectl >/dev/null 2>&1; then
    fail "kubectl not found in PATH"
    hint "macOS:  brew install kubectl"
    hint "Linux:  https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/"
else
    KUBECTL_VER="$(kubectl version --client 2>/dev/null | extract_semver)"
    if [ -z "$KUBECTL_VER" ]; then
        warn "kubectl found but client version could not be parsed"
        KUBECTL_OK=true
    elif version_ge "$KUBECTL_VER" "1.28"; then
        pass "kubectl ${KUBECTL_VER}  (>= 1.28 required)"
        KUBECTL_OK=true
    else
        fail "kubectl ${KUBECTL_VER} is too old — 1.28+ required"
        hint "Upgrade: https://kubernetes.io/docs/tasks/tools/"
    fi
fi

if ! command -v python3 >/dev/null 2>&1; then
    fail "python3 not found — required by sync_k8s_kit.py, patch_aws_creds.py, check_status.py"
    hint "macOS:  brew install python3"
    hint "Linux:  sudo apt-get install python3"
else
    PYTHON_VER="$(python3 --version 2>&1 | extract_semver)"
    if version_ge "${PYTHON_VER:-0.0}" "3.7"; then
        pass "python3 ${PYTHON_VER}"
    else
        fail "python3 ${PYTHON_VER} is too old — 3.7+ required"
        hint "Install a newer version: https://www.python.org/downloads/"
    fi
fi

if ! command -v aws >/dev/null 2>&1; then
    warn "aws CLI not found — required for 'make patch-aws-creds' and FL-client S3 kit access"
    hint "Install: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
else
    AWS_VER="$(aws --version 2>&1 | extract_semver || true)"
    pass "aws CLI ${AWS_VER:-found}"
    # Warn about stale SSO session early — patch-aws-creds is needed right after deploy
    if [ "$PROD" = "stag" ] || [ "$PROD" = "true" ]; then
        AWS_PROFILE_CHECK="${AWS_PROFILE:-flipstag}"
        if ! aws sts get-caller-identity --profile "$AWS_PROFILE_CHECK" >/dev/null 2>&1; then
            warn "AWS SSO session not active for profile '${AWS_PROFILE_CHECK}'"
            hint "Login before deploying: aws sso login --profile ${AWS_PROFILE_CHECK}"
            hint "(Required for 'make patch-aws-creds' to push FL-client S3 credentials)"
        else
            pass "AWS SSO session active  (profile: ${AWS_PROFILE_CHECK})"
        fi
    fi
fi

if ! command -v kubeconform >/dev/null 2>&1; then
    warn "kubeconform not installed — 'make validate' schema checks will be skipped"
    hint "macOS:  brew install kubeconform"
    hint "Linux:  go install github.com/yannh/kubeconform/cmd/kubeconform@latest"
    hint "Repo:   https://github.com/yannh/kubeconform"
fi

# ════════════════════════════════════════════════════════════════════════════════
section "2. Kubernetes cluster"
# ════════════════════════════════════════════════════════════════════════════════

CLUSTER_OK=false
if [ "$KUBECTL_OK" = "false" ]; then
    warn "Skipping cluster checks — kubectl is not available"
else
    CTX="$(kubectl config current-context 2>/dev/null || true)"
    if [ -z "$CTX" ]; then
        fail "No active kubectl context"
        hint "List contexts:  kubectl config get-contexts"
        hint "Set context:    kubectl config use-context <name>"
    else
        pass "Active context: ${CTX}"

        if ! kubectl cluster-info >/dev/null 2>&1; then
            fail "Cluster unreachable — check your kubeconfig, VPN/network access, or cloud credentials"
            hint "Diagnose with: kubectl cluster-info"
        else
            # Detect server version (output format changed in kubectl 1.26)
            SRV_VER="$(kubectl version 2>/dev/null | grep -i 'server' | extract_semver || true)"
            if [ -z "$SRV_VER" ]; then
                SRV_VER="$(kubectl version -o json 2>/dev/null \
                    | python3 -c \
                        'import sys,json; print(json.load(sys.stdin).get("serverVersion",{}).get("gitVersion",""))' \
                        2>/dev/null \
                    | extract_semver || true)"
            fi
            if [ -n "$SRV_VER" ] && version_ge "$SRV_VER" "1.28"; then
                pass "Server ${SRV_VER}  (>= 1.28 required)"
            elif [ -n "$SRV_VER" ]; then
                fail "Server ${SRV_VER} is too old — 1.28+ required"
                hint "Upgrade your cluster to Kubernetes 1.28+"
            else
                warn "Server version could not be verified — ensure the cluster is 1.28+"
            fi
            CLUSTER_OK=true
        fi
    fi
fi

if [ "$CLUSTER_OK" = "true" ]; then
    # Namespace creation permission
    if kubectl auth can-i create namespaces >/dev/null 2>&1; then
        pass "RBAC: can create namespaces"
    else
        warn "RBAC: cannot create namespaces — namespace '${NAMESPACE}' must already exist"
        hint "Pre-create it: kubectl create namespace ${NAMESPACE}"
    fi

    # Workload permissions in the target namespace
    if kubectl auth can-i create deployments --namespace "$NAMESPACE" >/dev/null 2>&1; then
        pass "RBAC: can create deployments in '${NAMESPACE}'"
    else
        warn "RBAC: cannot create deployments in '${NAMESPACE}' — Helm install may fail"
        hint "Contact your cluster administrator for a role binding in namespace '${NAMESPACE}'"
    fi

    # Secrets permission — hard requirement; Helm Secret for the release will fail without it
    if kubectl auth can-i create secrets --namespace "$NAMESPACE" >/dev/null 2>&1; then
        pass "RBAC: can create secrets in '${NAMESPACE}'"
    else
        fail "RBAC: cannot create secrets in '${NAMESPACE}' — Helm chart installation will fail"
        hint "Contact your cluster administrator"
    fi

    # Default StorageClass — PVCs for XNAT, OMOP, Orthanc need one
    DEFAULT_SC="$(kubectl get storageclass 2>/dev/null | awk '/\(default\)/{print $1}' | head -1)"
    if [ -n "$DEFAULT_SC" ]; then
        pass "Default StorageClass: ${DEFAULT_SC}"
    else
        warn "No default StorageClass found — chart PVCs will remain Pending without one"
        hint "List available classes: kubectl get storageclass"
        hint "Mark one as default:"
        hint "  kubectl patch storageclass <name> \\"
        hint "    -p '{\"metadata\":{\"annotations\":{\"storageclass.kubernetes.io/is-default-class\":\"true\"}}}'"
    fi
fi

# ════════════════════════════════════════════════════════════════════════════════
section "3. Chart files"
# ════════════════════════════════════════════════════════════════════════════════

if [ -f "${CHART_DIR}/values.yaml" ]; then
    pass "values.yaml"
else
    fail "values.yaml missing — expected at ${CHART_DIR}/values.yaml"
    hint "This file is bundled with the chart and must not be deleted"
fi

if [ -f "${CHART_DIR}/values-secrets.yaml" ]; then
    pass "values-secrets.yaml  (will be merged into deploy)"
else
    warn "values-secrets.yaml not found — XNAT/OMOP/S3 secrets will use chart defaults"
    hint "Patch secrets after the first deploy: make patch-kit-secrets KIT=<KIT> PROD=${PROD:-(env)}"
    hint "Or populate the file before deploying: ${CHART_DIR}/values-secrets.yaml"
fi

if [ -n "$OVERRIDES_FILE" ]; then
    if [ -f "$OVERRIDES_FILE" ]; then
        pass "OVERRIDES_FILE: ${OVERRIDES_FILE}"
    else
        fail "OVERRIDES_FILE not found: ${OVERRIDES_FILE}"
        hint "Generate with: make -C deploy/providers/kubernetes sync-kit KIT=<KIT> PROD=${PROD:-(env)}"
    fi
fi

# ════════════════════════════════════════════════════════════════════════════════
# 4. Trust kit — only checked when KIT is provided
# ════════════════════════════════════════════════════════════════════════════════
if [ -n "$KIT" ]; then
    section "4. Trust kit  (KIT=${KIT}, env=${ENV_SUFFIX})"

    KIT_FILE="${REPO_ROOT}/trust/.env.${KIT}.${ENV_SUFFIX}"
    K8S_OVERRIDE="${CHART_DIR}/k8s-trust-${KIT}.yaml"

    if [ ! -f "$KIT_FILE" ]; then
        fail "Kit file not found: ${KIT_FILE}"
        hint "Register the trust first (run from the repo root):"
        hint "  make new-trust TRUST_CODE=${KIT} TRUST_NAME=\"<Display Name>\""
        hint "  make -C deploy/providers/AWS register-trusts KIT=${KIT} PROD=${PROD:-(env)}"
        hint "  make sync-trust-kit KIT=${KIT} PROD=${PROD:-(env)}"
    else
        pass "Kit file: trust/.env.${KIT}.${ENV_SUFFIX}"

        # Required keys must not be empty or still a scaffolding placeholder (<...>)
        REQUIRED_KEYS=("TRUST_API_KEY" "AES_KEY_BASE64" "CENTRAL_HUB_API_URL")
        ALL_FILLED=true
        for key in "${REQUIRED_KEYS[@]}"; do
            val="$(grep -E "^${key}=" "$KIT_FILE" 2>/dev/null | head -1 \
                    | sed 's/^[^=]*=//' | tr -d '"'"'" || true)"
            if [ -z "$val" ] || [[ "$val" == "<"*">" ]]; then
                fail "Kit key ${key} is empty or still a placeholder in ${KIT_FILE##*/}"
                hint "Fill in real values by running the registration flow:"
                hint "  make -C deploy/providers/AWS register-trusts KIT=${KIT} PROD=${PROD:-(env)}"
                hint "  make sync-trust-kit KIT=${KIT} PROD=${PROD:-(env)}"
                ALL_FILLED=false
            fi
        done
        [ "$ALL_FILLED" = "true" ] \
            && pass "Kit keys: TRUST_API_KEY, AES_KEY_BASE64, CENTRAL_HUB_API_URL — all set"
    fi

    if [ ! -f "$K8S_OVERRIDE" ]; then
        warn "Per-trust override not yet generated: k8s-trust-${KIT}.yaml"
        hint "Generate it (and patch cluster secrets) with:"
        hint "  make -C deploy/providers/kubernetes sync-kit KIT=${KIT} PROD=${PROD:-(env)}"
        hint "Then deploy with OVERRIDES_FILE=k8s-trust-${KIT}.yaml"
    else
        pass "Per-trust override: k8s-trust-${KIT}.yaml"
    fi
fi

# ════════════════════════════════════════════════════════════════════════════════
section "5. Helm chart lint"
# ════════════════════════════════════════════════════════════════════════════════

if [ "$HELM_OK" = "true" ]; then
    if "$HELM_BIN" lint "$CHART_DIR" >/dev/null 2>&1; then
        pass "helm lint"
    else
        fail "helm lint failed — fix template errors before deploying"
        "$HELM_BIN" lint "$CHART_DIR" 2>&1 | sed 's/^/      /'
    fi
else
    warn "Skipping helm lint — helm is not available"
fi

# ════════════════════════════════════════════════════════════════════════════════
# Summary
# ════════════════════════════════════════════════════════════════════════════════
printf "\n${BOLD}────────────────────────────────────────────────────────────────${NC}\n"
if [ "$FAILURES" -gt 0 ]; then
    printf "${RED}${BOLD}  ✖  %d check(s) FAILED — resolve the issues above before deploying${NC}\n" "$FAILURES"
    [ "$WARNINGS" -gt 0 ] && \
        printf "${YELLOW}  ⚠  %d warning(s) — also review these before deploying${NC}\n" "$WARNINGS"
    printf "${BOLD}────────────────────────────────────────────────────────────────${NC}\n\n"
    exit 1
elif [ "$WARNINGS" -gt 0 ]; then
    printf "${YELLOW}${BOLD}  ⚠  %d warning(s) — deployment may succeed; review items above${NC}\n" "$WARNINGS"
    printf "${BOLD}────────────────────────────────────────────────────────────────${NC}\n\n"
    exit 0
else
    printf "${GREEN}${BOLD}  ✔  All checks passed — ready to deploy${NC}\n"
    printf "${BOLD}────────────────────────────────────────────────────────────────${NC}\n\n"
    exit 0
fi
