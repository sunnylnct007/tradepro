# ============================================================
# Production frontend image: Vite build → nginx serving the static
# bundle on port 80, proxying /api/* and /health to the api container.
#
# Built in CI by .github/workflows/aws-build-push.yml. Tagged ARM64
# for the t4g.small Graviton host.
#
# Why nginx-in-front (not direct browser → API on port 8081):
#   - Single URL: http://<EIP>/ for the user (no port to remember,
#     no CORS to manage)
#   - The API container doesn't need a public port at all once the
#     frontend is the entry point — but we keep :8081 mapped for
#     debugging / direct API access from curl
#   - Same pattern energycosmos uses (gateway nginx in front of
#     all services on port 8080)
# ============================================================

# ── Build stage ───────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /app

# Install deps in a layer that only invalidates when package*.json
# changes — keeps subsequent code-only builds fast.
COPY package.json package-lock.json* ./
RUN npm ci

COPY . .

# Empty VITE_API_BASE_URL → the frontend issues relative requests
# (`/api/marketdata/candles`) which nginx proxies to the api
# container. No CORS, no IP-baked-into-bundle.
ENV VITE_API_BASE_URL=""

RUN npm run build

# ── Runtime stage ─────────────────────────────────────────────
FROM nginx:1.27-alpine AS runtime

# Replace the default nginx config with our SPA + API-proxy config.
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# nginx:alpine's default CMD already runs nginx in the foreground.
