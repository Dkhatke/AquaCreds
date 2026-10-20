"""Conversions between platform identifiers and Solidity types, plus JSON-safe argument storage.

Transaction arguments are persisted as JSON (bytes → 0x-hex) and converted back to ABI types just
before signing, so a queued transaction is fully inspectable in the database."""

import hashlib
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import rfc8785
from eth_utils.crypto import keccak

_HEX32 = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")


def project_id_bytes32(project_id: uuid.UUID | str) -> bytes:
    """On-chain project id: keccak256 of the canonical (lower-case, hyphenated) UUID string."""
    return keccak(text=str(uuid.UUID(str(project_id))))


def hex_to_bytes32(value: str) -> bytes:
    """A 32-byte hash given as hex (with or without 0x), e.g. a report's SHA-256."""
    if not _HEX32.match(value):
        raise ValueError(f"expected 32 bytes of hex, got {value!r}")
    return bytes.fromhex(value.removeprefix("0x"))


def to_hex(value: bytes) -> str:
    return "0x" + value.hex()


def geojson_hash(geometry: Mapping[str, Any]) -> bytes:
    """SHA-256 of the RFC 8785 canonical JSON of a GeoJSON geometry (the boundary hash anchored
    on-chain). Anyone with the published GeoJSON can recompute it."""
    return hashlib.sha256(rfc8785.dumps(dict(geometry))).digest()


def _is_bytes_type(abi_type: str) -> bool:
    return abi_type == "bytes" or (abi_type.startswith("bytes") and abi_type[5:].isdigit())


def to_json_arg(value: Any) -> Any:
    """Make a Python ABI value JSON-safe (bytes → 0x-hex; tuples → lists)."""
    if isinstance(value, bytes | bytearray):
        return "0x" + bytes(value).hex()
    if isinstance(value, Mapping):
        return {str(k): to_json_arg(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_json_arg(v) for v in value]
    return value


def from_json_args(inputs: Sequence[Mapping[str, Any]], args: Sequence[Any]) -> list[Any]:
    """Convert stored JSON args back to ABI values using the function's ABI `inputs`."""
    if len(inputs) != len(args):
        raise ValueError(f"expected {len(inputs)} arguments, got {len(args)}")
    out: list[Any] = []
    for spec, value in zip(inputs, args, strict=True):
        abi_type = str(spec["type"])
        if _is_bytes_type(abi_type):
            if not isinstance(value, str) or not value.startswith("0x"):
                raise ValueError(f"{spec.get('name')}: expected 0x-hex for {abi_type}")
            out.append(bytes.fromhex(value[2:]))
        elif abi_type.startswith(("uint", "int")):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{spec.get('name')}: expected an integer for {abi_type}")
            out.append(value)
        else:
            out.append(value)
    return out
