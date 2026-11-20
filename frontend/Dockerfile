# syntax=docker/dockerfile:1
# REGISTRY lets you pull official images through a mirror (e.g. mirror.gcr.io/library/)
ARG REGISTRY=

# ---- build: install deps and produce the static bundle ----
FROM ${REGISTRY}node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Optional build-time default; the runtime API_BASE_URL (below) takes precedence.
ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# ---- runtime: nginx serving static files (~50 MB image, no Node) ----
FROM ${REGISTRY}nginx:stable-alpine AS runtime
ENV API_BASE_URL=http://localhost:8000
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.d/40-runtime-config.sh /docker-entrypoint.d/40-runtime-config.sh
RUN chmod +x /docker-entrypoint.d/40-runtime-config.sh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s CMD wget -qO- http://127.0.0.1:3000/ >/dev/null || exit 1
