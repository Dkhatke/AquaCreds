"""The committed ABIs must match a fresh Foundry build (Foundry runs in Docker via forge.sh)."""

import shutil

import pytest

from aqua_chain import abi_export


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not (shutil.which("forge") or _docker_available()),
    reason="needs Foundry or Docker to build the contracts",
)
def test_committed_abis_match_a_fresh_build() -> None:
    contracts_dir = abi_export.repo_root() / "contracts"
    abi_export.build(contracts_dir)
    problems = abi_export.drift(contracts_dir)
    assert problems == [], (
        f"ABIs drifted from contracts/src: {problems}. "
        "Run `uv run python -m aqua_chain.abi_export` and commit the result."
    )
