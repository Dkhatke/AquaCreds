#!/usr/bin/env sh
# Run Foundry's `forge`: a local install if present (foundryup), otherwise the pinned Docker image.
# The container is removed after each command (--rm); the compiler cache lives in a named volume.
#   ./forge.sh soldeer install   ./forge.sh build   ./forge.sh test -vv   ./forge.sh coverage
set -eu
cd "$(dirname "$0")"
if command -v forge >/dev/null 2>&1; then
  exec forge "$@"
fi
# Forward the deploy script's role/fee settings (script/Deploy.s.sol reads them with vm.envOr) and
# the explorer key for --verify: `docker run` does not pass the host environment otherwise, and the
# roles would silently default to the deployer (verifier: 0xBEEF). `-e NAME` forwards only if set.
exec docker run --rm \
  -e ADMIN_ADDRESS -e REGISTRAR_ADDRESS -e VERIFIER_ADDRESS -e ISSUER_ADDRESS \
  -e TREASURY_ADDRESS -e FEE_BPS -e CREDIT_URI -e ETHERSCAN_API_KEY \
  -v "$PWD":/work -w /work \
  -v aquacreds-foundry-cache:/root/.svm \
  -e HOME=/root \
  --user root \
  --entrypoint forge \
  ghcr.io/foundry-rs/foundry:v1.7.0 "$@"
