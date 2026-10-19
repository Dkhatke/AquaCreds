#!/usr/bin/env bash
# `docker compose` for the production stack with the right env file, compose file and image tag.
#
#   infra/scripts/compose.sh ps
#   infra/scripts/compose.sh logs -f --tail 100 api
#   infra/scripts/compose.sh exec api aqua-api create-user --email admin@example.org
#   infra/scripts/compose.sh --profile monitoring up -d uptime-kuma
set -euo pipefail

# shellcheck source=infra/scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_env_file
dc "$@"
