"""Decode raw logs of the platform contracts into JSON-safe event records."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from eth_utils.crypto import keccak
from web3.contract.async_contract import AsyncContractEvent

from aqua_chain import artifacts
from aqua_chain.client import ChainClient
from aqua_chain.encoding import to_json_arg
from aqua_chain.errors import canonical_type


@dataclass(frozen=True)
class DecodedEvent:
    contract: str
    event: str
    address: str
    block_number: int
    block_hash: str
    tx_hash: str
    log_index: int
    args: dict[str, Any]

    @property
    def project_key(self) -> str | None:
        value = self.args.get("projectId")
        return value if isinstance(value, str) else None


def _hex(value: Any) -> str:
    if isinstance(value, bytes | bytearray):
        return "0x" + bytes(value).hex()
    text = str(value)
    return text if text.startswith("0x") else "0x" + text


def event_topic(entry: Mapping[str, Any]) -> str:
    types = ",".join(canonical_type(p) for p in entry.get("inputs", []))
    return "0x" + keccak(text=f"{entry['name']}({types})").hex()


class EventDecoder:
    """topic0 → event handle, per configured contract address."""

    def __init__(self, client: ChainClient, contracts: tuple[str, ...] | None = None) -> None:
        names = contracts or tuple(client.addresses)
        self._by_address: dict[str, tuple[str, dict[str, tuple[str, AsyncContractEvent]]]] = {}
        for name in names:
            if name not in client.addresses:
                continue
            contract = client.contract(name)
            topics = {}
            for entry in artifacts.abi(name):
                if entry.get("type") == "event" and not entry.get("anonymous"):
                    handle = getattr(contract.events, entry["name"])()
                    topics[event_topic(entry)] = (entry["name"], handle)
            self._by_address[str(contract.address).lower()] = (name, topics)

    def decode(self, log: Mapping[str, Any]) -> DecodedEvent | None:
        known = self._by_address.get(str(log["address"]).lower())
        if known is None or not log.get("topics"):
            return None
        contract_name, topics = known
        match = topics.get(_hex(log["topics"][0]).lower())
        if match is None:
            return None
        event_name, handle = match
        data = handle.process_log(log)
        return DecodedEvent(
            contract=contract_name,
            event=event_name,
            address=str(log["address"]),
            block_number=int(log["blockNumber"]),
            block_hash=_hex(log["blockHash"]),
            tx_hash=_hex(log["transactionHash"]),
            log_index=int(log["logIndex"]),
            args={str(k): to_json_arg(v) for k, v in dict(data["args"]).items()},
        )
