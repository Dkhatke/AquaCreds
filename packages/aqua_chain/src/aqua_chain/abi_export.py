"""Export contract ABIs (+ bytecode, used by tests to deploy on Anvil) from a Foundry build.

    uv run python -m aqua_chain.abi_export           # build with contracts/forge.sh, then copy
    uv run python -m aqua_chain.abi_export --check   # exit 1 if the committed files drifted

The committed JSON files under `aqua_chain/abis/` are the single source the backend uses, so the
API/worker images never need Foundry. A test runs `--check` (when Docker is available) so a
contract change without a re-export fails CI.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

CONTRACTS = ("AquaRegistry", "AquaCredit", "AquaMarketplace", "MockUSDC", "AquaForwardFund")
ABI_DIR = Path(__file__).parent / "abis"


def repo_root() -> Path:
    # packages/aqua_chain/src/aqua_chain/abi_export.py → repository root is parents[4]
    return Path(__file__).resolve().parents[4]


def build(contracts_dir: Path) -> None:
    forge = contracts_dir / "forge.sh"
    if not (contracts_dir / "dependencies").is_dir():
        subprocess.run([str(forge), "soldeer", "install"], check=True, cwd=contracts_dir)  # noqa: S603
    subprocess.run([str(forge), "build"], check=True, cwd=contracts_dir)  # noqa: S603


def strip_metadata(bytecode: str) -> str:
    """Drop the trailing CBOR metadata (its length is the last 2 bytes) so the comparison ignores
    compiler-environment noise but still catches any code change."""
    code = bytecode.removeprefix("0x")
    if len(code) < 4:
        return code
    meta_len = int(code[-4:], 16)
    cut = len(code) - 4 - meta_len * 2
    return code[:cut] if cut > 0 else code


def artifact(contracts_dir: Path, name: str) -> dict[str, Any]:
    path = contracts_dir / "out" / f"{name}.sol" / f"{name}.json"
    raw = json.loads(path.read_text())
    return {
        "contractName": name,
        "abi": raw["abi"],
        "bytecode": raw["bytecode"]["object"],
    }


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=1, sort_keys=True) + "\n"


def export(contracts_dir: Path, out_dir: Path = ABI_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in CONTRACTS:
        target = out_dir / f"{name}.json"
        target.write_text(render(artifact(contracts_dir, name)))
        written.append(target)
    return written


def drift(contracts_dir: Path, out_dir: Path = ABI_DIR) -> list[str]:
    """Names of contracts whose committed ABI/bytecode differ from the build in contracts/out."""
    problems = []
    for name in CONTRACTS:
        fresh = artifact(contracts_dir, name)
        committed_path = out_dir / f"{name}.json"
        if not committed_path.exists():
            problems.append(f"{name}: missing")
            continue
        committed = json.loads(committed_path.read_text())
        if committed["abi"] != fresh["abi"]:
            problems.append(f"{name}: ABI differs")
        elif strip_metadata(committed["bytecode"]) != strip_metadata(fresh["bytecode"]):
            problems.append(f"{name}: bytecode differs")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if committed ABIs drifted")
    parser.add_argument("--no-build", action="store_true", help="reuse an existing contracts/out")
    args = parser.parse_args(argv)
    contracts_dir = repo_root() / "contracts"
    if not args.no_build:
        build(contracts_dir)
    if args.check:
        problems = drift(contracts_dir)
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    for path in export(contracts_dir):
        print(path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
