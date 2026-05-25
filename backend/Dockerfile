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

# Restore the API project only. The .sln also references
# TradePro.Api.Tests/ now (xUnit + Testcontainers, OMS Phase 1a) but
# tests don't belong in the runtime image — building the sln would
# fail here because the tests csproj isn't COPY'd in.
COPY TradePro.Api/TradePro.Api.csproj TradePro.Api/
RUN dotnet restore TradePro.Api/TradePro.Api.csproj

# Source + publish. COPY only the API source to keep the image lean
# and avoid pulling in tests on the next layer change.
COPY TradePro.Api/ TradePro.Api/
RUN dotnet publish TradePro.Api/TradePro.Api.csproj \
    --configuration Release \
    --no-restore \
    --output /app/publish

# ── Runtime stage ─────────────────────────────────────────────
FROM ${DOTNET_RUNTIME} AS runtime
WORKDIR /app

# `curl` is needed for the compose healthcheck. apt is fine here —
# the runtime image already has the package manager set up.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /app/publish .

# Listen on 5080 inside the container (compose maps host:8081 → 5080).
# Production environment so the API requires real auth, real CORS, etc.
ENV ASPNETCORE_URLS=http://+:5080 \
    ASPNETCORE_ENVIRONMENT=Production \
    DOTNET_RUNNING_IN_CONTAINER=true \
    DOTNET_PRINT_TELEMETRY_MESSAGE=false

EXPOSE 5080

# Run as the non-root `app` user that the runtime image already
# provisions — least privilege inside the container.
USER app

ENTRYPOINT ["dotnet", "TradePro.Api.dll"]
