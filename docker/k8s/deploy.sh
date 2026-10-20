#!/usr/bin/env bash
# Build the image here, import it into the VPS's k3s, apply the manifests and the nginx route.
# Usage: docker/k8s/deploy.sh [ssh-host]   (default: VPS_HOST=root@<server> in docker/.env, which git ignores)
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -f docker/.env ] && VPS_HOST="${VPS_HOST:-$(sed -n 's/^VPS_HOST=//p' docker/.env)}"
HOST="${1:-${VPS_HOST:?pass the ssh host or set VPS_HOST in docker/.env}}"
TAG="$(git rev-parse --short HEAD)$(git diff --quiet HEAD -- . ':!projects' || echo -dirty)"
docker build -f docker/Dockerfile -t "hwflow:$TAG" .
echo "uploading hwflow:$TAG"
docker save "hwflow:$TAG" | zstd -T0 -3 -q | ssh "$HOST" 'zstd -dq | k3s ctr -n k8s.io images import - >/dev/null'
sed "s/IMAGE_TAG/$TAG/" docker/k8s/hwflow.yaml | ssh "$HOST" 'kubectl apply -f -'
ssh "$HOST" 'mkdir -p /etc/nginx/apps.d /var/www/apps'
scp -q docker/k8s/landing/index.html "$HOST:/var/www/apps/index.html"
scp -q docker/k8s/nginx-apps.conf "$HOST:/etc/nginx/sites-enabled/apps"
scp -q docker/k8s/nginx-hwflow.conf "$HOST:/etc/nginx/apps.d/hwflow.conf"
ssh "$HOST" 'nginx -t -q && systemctl reload nginx && kubectl -n hwflow rollout status deploy/hwflow --timeout=300s
  # keep the two newest hwflow images
  k3s crictl images | awk "\$1==\"docker.io/library/hwflow\"{print \$3}" | tail -n +3 | xargs -r k3s crictl rmi >/dev/null 2>&1 || true'
