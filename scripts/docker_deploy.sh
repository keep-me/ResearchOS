#!/usr/bin/env bash
# ResearchOS Docker quick deploy script
# Usage: ./scripts/docker_deploy.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"
ENV_TEMPLATE="$PROJECT_ROOT/.env.example"

printf "========================================\n"
printf "ResearchOS Docker Deploy\n"
printf "========================================\n\n"

printf "[1/7] Checking env file...\n"
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ ! -f "$ENV_TEMPLATE" ]]; then
    printf "Error: missing env template: %s\n" "$ENV_TEMPLATE"
    exit 1
  fi
  cp "$ENV_TEMPLATE" "$ENV_FILE"
  printf "Created %s from template.\n" "$ENV_FILE"
  printf "Please update required keys before production use.\n\n"
fi

printf "[2/7] Checking Docker...\n"
if ! command -v docker >/dev/null 2>&1; then
  printf "Error: docker not found.\n"
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  printf "Error: docker compose not available.\n"
  exit 1
fi

printf "[3/7] Stopping old containers (if any)...\n"
cd "$PROJECT_ROOT"
docker compose down >/dev/null 2>&1 || true

printf "[4/7] Building images...\n"
docker compose build

printf "[5/7] Starting services...\n"
docker compose up -d

printf "[6/7] Service status:\n"
docker compose ps

printf "\n[7/7] Helpful commands:\n"
printf "  Frontend:   http://localhost:3002\n"
printf "  Backend:    http://localhost:8002\n"
printf "  API Docs:   http://localhost:8002/docs\n"
printf "  Logs:       docker compose logs -f\n"
printf "  Stop:       docker compose down\n"
printf "========================================\n"
