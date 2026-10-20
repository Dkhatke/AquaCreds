"""Committed contract artifacts (ABI + bytecode), exported from the Foundry build by
`aqua_chain.abi_export`."""

import json
from functools import cache
from typing import Any, Literal

from aqua_chain.abi_export import ABI_DIR, CONTRACTS

ContractName = Literal["AquaRegistry", "AquaCredit", "AquaMarketplace", "MockUSDC"]


@cache
def load(name: str) -> dict[str, Any]:
    if name not in CONTRACTS:
        raise KeyError(f"unknown contract {name!r}; expected one of {CONTRACTS}")
    data: dict[str, Any] = json.loads((ABI_DIR / f"{name}.json").read_text())
    return data


def abi(name: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = load(name)["abi"]
    return entries


def bytecode(name: str) -> str:
    code: str = load(name)["bytecode"]
    return code


def function_abi(name: str, function: str) -> dict[str, Any]:
    matches = [e for e in abi(name) if e.get("type") == "function" and e["name"] == function]
    if len(matches) != 1:
        raise KeyError(f"{name}.{function}: expected exactly one ABI entry, found {len(matches)}")
    return matches[0]
