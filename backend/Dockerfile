# ============================================================
# Production .NET 8 image for the TradePro API.
#
# Built in CI by .github/workflows/aws-build-push.yml and pushed to
# ECR. The AWS host pulls + runs via docker-compose.aws.yaml.
#
# Multi-stage:
#   - build:    SDK image, restore + publish trimmed self-contained
#   - runtime:  ASP.NET Core 8 runtime image, only the publish output
#
# Target: linux/arm64 (matches the t4g.small Graviton host). The build
# is forced via Buildx --platform in CI so this Dockerfile doesn't need
# to set TargetArch.
# ============================================================

ARG DOTNET_SDK=mcr.microsoft.com/dotnet/sdk:8.0
ARG DOTNET_RUNTIME=mcr.microsoft.com/dotnet/aspnet:8.0

# ── Build stage ───────────────────────────────────────────────
FROM ${DOTNET_SDK} AS build
WORKDIR /src

# Build context is repo root (set in aws-build-push.yml).
# Restore the API project only.
COPY backend/TradePro.Api/TradePro.Api.csproj TradePro.Api/
RUN dotnet restore TradePro.Api/TradePro.Api.csproj

COPY backend/TradePro.Api/ TradePro.Api/
RUN dotnet publish TradePro.Api/TradePro.Api.csproj \
    --configuration Release \
    --no-restore \
    --output /app/publish

# ── Runtime stage ─────────────────────────────────────────────
FROM ${DOTNET_RUNTIME} AS runtime
WORKDIR /app

# curl = healthcheck; python3 + pip = screener subprocess (daily_run.py)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Install screener Python deps into a venv (keeps system Python clean)
COPY screener/requirements.txt /screener/requirements.txt
RUN python3 -m venv /screener/.venv \
    && /screener/.venv/bin/pip install --no-cache-dir -r /screener/requirements.txt

# Copy screener scripts
COPY screener/ /screener/

COPY --from=build /app/publish .

# Listen on 5080 inside the container (compose maps host:8081 → 5080).
# Production environment so the API requires real auth, real CORS, etc.
# Build provenance — set by CI via --build-arg in aws-build-push.yml.
# Surfaces in /health/details so the operator can verify "is the
# running container actually the commit I just pushed?" without
# SSM-ing into the host.
ARG BACKEND_COMMIT=unknown
ARG BACKEND_BUILD_TIME=unknown
ENV BACKEND_COMMIT=${BACKEND_COMMIT} \
    BACKEND_BUILD_TIME=${BACKEND_BUILD_TIME} \
    ASPNETCORE_URLS=http://+:5080 \
    ASPNETCORE_ENVIRONMENT=Production \
    DOTNET_RUNNING_IN_CONTAINER=true \
    DOTNET_PRINT_TELEMETRY_MESSAGE=false \
    MPLBACKEND=Agg \
    SCREENER_PYTHON=/screener/.venv/bin/python3

EXPOSE 5080

# Run as the non-root `app` user that the runtime image already
# provisions — least privilege inside the container.
USER app

ENTRYPOINT ["dotnet", "TradePro.Api.dll"]
