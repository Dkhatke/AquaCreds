"""Revert decoding (custom errors, `Error(string)`, `Panic(uint256)`) and relayer exceptions."""

from dataclasses import dataclass, field
from functools import cache
from typing import Any

from eth_abi.abi import decode as abi_decode
from eth_utils.crypto import keccak

from aqua_chain import artifacts
from aqua_chain.abi_export import CONTRACTS
from aqua_chain.encoding import to_json_arg

_ERROR_STRING = bytes.fromhex("08c379a0")  # Error(string)
_PANIC = bytes.fromhex("4e487b71")  # Panic(uint256)


class ChainError(Exception):
    """Base class for chain integration errors."""


class RetryableChainError(ChainError):
    """Transient: try again later (RPC down, gas price above the cap...)."""


class GasPriceTooHighError(RetryableChainError):
    pass


class ChainIdMismatchError(ChainError):
    pass


def canonical_type(param: dict[str, Any]) -> str:
    if param["type"].startswith("tuple"):
        inner = ",".join(canonical_type(c) for c in param["components"])
        return f"({inner}){param['type'][5:]}"
    return str(param["type"])


@cache
def error_selectors() -> dict[bytes, tuple[str, list[str], list[str]]]:
    """4-byte selector → (error name, arg types, arg names) across all platform contracts."""
    selectors: dict[bytes, tuple[str, list[str], list[str]]] = {}
    for name in CONTRACTS:
        for entry in artifacts.abi(name):
            if entry.get("type") != "error":
                continue
            types = [canonical_type(p) for p in entry.get("inputs", [])]
            names = [p.get("name") or f"arg{i}" for i, p in enumerate(entry.get("inputs", []))]
            signature = f"{entry['name']}({','.join(types)})"
            selectors[keccak(text=signature)[:4]] = (entry["name"], types, names)
    return selectors


@dataclass(frozen=True)
class DecodedRevert:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    raw: str = ""

    def describe(self) -> str:
        if not self.args:
            return f"{self.name}()"
        inner = ", ".join(f"{k}={v}" for k, v in self.args.items())
        return f"{self.name}({inner})"


def _as_bytes(data: Any) -> bytes | None:
    if isinstance(data, bytes | bytearray):
        return bytes(data)
    if isinstance(data, dict):
        data = data.get("data")
    if isinstance(data, str) and data.startswith("0x"):
        try:
            return bytes.fromhex(data[2:])
        except ValueError:
            return None
    return None


def decode_revert(data: Any) -> DecodedRevert | None:
    """Decode revert data into the error name and arguments, or None if unrecognised."""
    payload = _as_bytes(data)
    if payload is None or len(payload) < 4:
        return None
    selector, body = payload[:4], payload[4:]
    raw = "0x" + payload.hex()
    try:
        if selector == _ERROR_STRING:
            (message,) = abi_decode(["string"], body)
            return DecodedRevert("Error", {"message": message}, raw)
        if selector == _PANIC:
            (code,) = abi_decode(["uint256"], body)
            return DecodedRevert("Panic", {"code": hex(code)}, raw)
        known = error_selectors().get(selector)
        if known is None:
            return None
        name, types, names = known
        values = abi_decode(types, body) if types else ()
    except Exception:  # malformed payload: report as undecodable
        return None
    return DecodedRevert(name, {n: to_json_arg(v) for n, v in zip(names, values, strict=True)}, raw)
