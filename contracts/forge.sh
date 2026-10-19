#!/usr/bin/env sh
# Run Foundry's `forge`: a local install if present (foundryup), otherwise the pinned Docker image.
# The container is removed after each command (--rm); the compiler cache lives in a named volume.
#   ./forge.sh soldeer install   ./forge.sh build   ./forge.sh test -vv   ./forge.sh coverage
set -eu
cd "$(dirname "$0")"
if command -v forge >/dev/null 2>&1; then
  exec forge "$@"
fi
exec docker run --rm \
  -v "$PWD":/work -w /work \
  -v aquacreds-foundry-cache:/root/.svm \
  -e HOME=/root \
  --user root \
  --entrypoint forge \
  ghcr.io/foundry-rs/foundry:v1.7.0 "$@"
