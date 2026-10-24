# Web UI: node builds the SPA, nginx (unprivileged, slim, ~20 MB) serves it and proxies /api to the API.
# Build from the repository root (the API schema lives in services/platform):
#   docker build -f web/Dockerfile -t hwflow-web .
FROM node:22.22-alpine AS build
WORKDIR /repo
COPY services/platform/openapi.json services/platform/openapi.json
WORKDIR /repo/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# Path prefix of the app (docs/platform-k8s.md): --build-arg WEB_BASE=/platform/ behind the VPS host nginx.
ARG WEB_BASE=/
RUN npm run build -- --base "$WEB_BASE"

FROM nginxinc/nginx-unprivileged:1.29.3-alpine-slim
ENV API_UPSTREAM=http://api:8080 \
    CSP_CONNECT_EXTRA=""
COPY web/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /repo/web/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1
