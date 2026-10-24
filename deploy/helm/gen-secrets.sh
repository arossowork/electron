#!/usr/bin/env bash
# Prints the kubectl commands that create the Secret(s) the hwflow-platform chart reads. It generates fresh random
# values and PRINTS the commands; it applies nothing. Review, then run them yourself (or pipe to sh):
#   deploy/helm/gen-secrets.sh hwflow-platform > /tmp/create-secrets.sh   # namespace as the first argument
#   less /tmp/create-secrets.sh && sh /tmp/create-secrets.sh
# Keep a copy of the master key outside the cluster: losing it makes every stored customer LLM key unreadable.
set -euo pipefail
ns="${1:-hwflow-platform}"
name="${2:-hwflow-platform-secrets}"
hex() { openssl rand -hex "${1:-24}"; }   # hex only: the passwords go into database URLs unescaped

cat <<EOF
kubectl get ns ${ns} >/dev/null 2>&1 || kubectl create namespace ${ns}
kubectl -n ${ns} create secret generic ${name} \\
  --from-literal=postgres-password=$(hex) \\
  --from-literal=hwflow-db-password=$(hex) \\
  --from-literal=hwflow-app-db-password=$(hex) \\
  --from-literal=hwflow-relay-db-password=$(hex) \\
  --from-literal=temporal-db-password=$(hex) \\
  --from-literal=jwt-secret=$(hex 32) \\
  --from-literal=master-key=$(openssl rand -base64 32) \\
  --from-literal=s3-access-key=hwf$(hex 8) \\
  --from-literal=s3-secret-key=$(hex 24)
# Optional, only for an external backup target (backup.target.secretName):
#   kubectl -n ${ns} create secret generic hwflow-backup-target --from-literal=access-key=... --from-literal=secret-key=...
EOF
