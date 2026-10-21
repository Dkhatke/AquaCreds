"""Unit tests: identifiers, argument encoding, HD derivation, revert decoding, status machine."""

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from eth_abi.abi import encode
from eth_utils.crypto import keccak

from aqua_chain import abi_export, artifacts
from aqua_chain.encoding import (
    from_json_args,
    geojson_hash,
    hex_to_bytes32,
    project_id_bytes32,
    to_hex,
    to_json_arg,
)
from aqua_chain.errors import decode_revert, error_selectors
from aqua_chain.events import event_topic
from aqua_chain.relayer import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    InvalidTransitionError,
    RelayerPolicy,
    nonce_lock_key,
    transition,
)
from aqua_chain.wallets import ChainNotConfiguredError, Signers, derivation_path
from aqua_core.models import ChainSigner, ChainTxStatus
from aqua_core.settings import ANVIL_DEV_MNEMONIC, Settings

# Well-known Anvil/Hardhat accounts for the public dev mnemonic.
ANVIL_ADDRESSES = [
    "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
    "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
    "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
    "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
]


# ---------------------------------------------------------------- identifiers & encoding


def test_project_id_is_keccak_of_canonical_uuid_string() -> None:
    pid = uuid.UUID("0b9f3c1e-5d6a-4f1b-9a3e-2c4d5e6f7a8b")
    expected = keccak(text="0b9f3c1e-5d6a-4f1b-9a3e-2c4d5e6f7a8b")
    assert project_id_bytes32(pid) == expected
    assert project_id_bytes32(str(pid).upper()) == expected  # normalised
    assert len(expected) == 32


def test_hex_to_bytes32_accepts_prefixed_and_bare_hex() -> None:
    digest = "ab" * 32
    assert hex_to_bytes32(digest) == bytes.fromhex(digest)
    assert hex_to_bytes32("0x" + digest) == bytes.fromhex(digest)
    assert to_hex(hex_to_bytes32(digest)) == "0x" + digest


@pytest.mark.parametrize("bad", ["ab" * 31, "zz" * 32, "0x" + "ab" * 33, ""])
def test_hex_to_bytes32_rejects_wrong_length_or_chars(bad: str) -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        hex_to_bytes32(bad)


def test_geojson_hash_is_canonical() -> None:
    a = {"type": "Polygon", "coordinates": [[[1, 2], [3, 4], [1, 2]]]}
    b = {"coordinates": [[[1, 2], [3, 4], [1, 2]]], "type": "Polygon"}
    assert geojson_hash(a) == geojson_hash(b)
    assert len(geojson_hash(a)) == 32


def test_json_args_roundtrip_through_abi_inputs() -> None:
    inputs = artifacts.function_abi("AquaRegistry", "submitReport")["inputs"]
    native = [b"\x01" * 32, 2028, b"\x02" * 32, -5, "ipfs://x"]
    stored = to_json_arg(native)
    assert stored[0] == "0x" + "01" * 32
    assert json.loads(json.dumps(stored)) == stored
    assert from_json_args(inputs, stored) == native


def test_json_args_validation() -> None:
    inputs = artifacts.function_abi("AquaRegistry", "approveReport")["inputs"]
    with pytest.raises(ValueError, match="expected 1 arguments"):
        from_json_args(inputs, [])
    with pytest.raises(ValueError, match="expected an integer"):
        from_json_args(inputs, ["1"])
    with pytest.raises(ValueError, match="expected an integer"):
        from_json_args(inputs, [True])
    bytes_inputs = artifacts.function_abi("AquaRegistry", "registerProject")["inputs"]
    with pytest.raises(ValueError, match="0x-hex"):
        from_json_args(bytes_inputs, ["01" * 32, "0x0", "0x" + "00" * 32, 2026, "u"])


def test_to_json_arg_handles_nested_values() -> None:
    assert to_json_arg({"a": (b"\x0f", 3)}) == {"a": ["0x0f", 3]}


def test_artifacts() -> None:
    assert artifacts.bytecode("AquaRegistry").startswith("0x60")
    with pytest.raises(KeyError, match="unknown contract"):
        artifacts.load("Nope")
    with pytest.raises(KeyError, match="exactly one"):
        artifacts.function_abi("AquaRegistry", "nope")


# ---------------------------------------------------------------- wallets


def _signers(**over: Any) -> Signers:
    params: dict[str, Any] = {
        "registrar_index": 1,
        "verifier_index": 2,
        "issuer_index": 3,
        "org_index_offset": 1000,
    }
    params.update(over)
    return Signers(ANVIL_DEV_MNEMONIC, **params)


def test_signers_derive_known_anvil_accounts() -> None:
    s = _signers()
    assert [s.derive(i).address for i in range(4)] == ANVIL_ADDRESSES
    assert s.addresses() == {
        ChainSigner.REGISTRAR: ANVIL_ADDRESSES[1],
        ChainSigner.VERIFIER: ANVIL_ADDRESSES[2],
        ChainSigner.ISSUER: ANVIL_ADDRESSES[3],
    }


def test_registrar_and_verifier_are_different_accounts() -> None:
    s = Signers.from_settings(Settings(_env_file=None, relayer_mnemonic=ANVIL_DEV_MNEMONIC))
    assert s.address(ChainSigner.REGISTRAR) != s.address(ChainSigner.VERIFIER)
    with pytest.raises(ValueError, match="different keys"):
        _signers(verifier_index=1)


def test_signers_require_a_mnemonic() -> None:
    with pytest.raises(ChainNotConfiguredError):
        Signers("  ", registrar_index=1, verifier_index=2, issuer_index=3, org_index_offset=9)


def test_derivation_path_bounds() -> None:
    assert derivation_path(7) == "m/44'/60'/0'/0/7"
    with pytest.raises(ValueError, match="derivation index"):
        derivation_path(-1)
    with pytest.raises(ValueError, match="derivation index"):
        derivation_path(2**31)


# ---------------------------------------------------------------- revert decoding


def _selector(sig: str) -> bytes:
    return keccak(text=sig)[:4]


def test_decode_custom_error_without_args() -> None:
    decoded = decode_revert("0x" + _selector("SelfApproval()").hex())
    assert decoded is not None
    assert decoded.name == "SelfApproval"
    assert decoded.describe() == "SelfApproval()"


def test_decode_custom_error_with_args() -> None:
    project = b"\x07" * 32
    data = _selector("ProjectExists(bytes32)") + encode(["bytes32"], [project])
    decoded = decode_revert({"data": "0x" + data.hex()})
    assert decoded is not None
    assert decoded.name == "ProjectExists"
    assert decoded.args == {"projectId": "0x" + "07" * 32}
    assert "projectId=0x0707" in decoded.describe()


def test_decode_openzeppelin_access_control_error() -> None:
    account, role = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8", b"\x01" * 32
    data = _selector("AccessControlUnauthorizedAccount(address,bytes32)") + encode(
        ["address", "bytes32"], [account, role]
    )
    decoded = decode_revert(data)
    assert decoded is not None
    assert decoded.name == "AccessControlUnauthorizedAccount"
    assert decoded.args["account"].lower() == account.lower()


def test_decode_error_string_and_panic() -> None:
    err = decode_revert(_selector("Error(string)") + encode(["string"], ["nope"]))
    assert err is not None
    assert err.args == {"message": "nope"}
    panic = decode_revert(_selector("Panic(uint256)") + encode(["uint256"], [0x11]))
    assert panic is not None
    assert panic.args == {"code": "0x11"}


@pytest.mark.parametrize("data", [None, "0x", "0x1234", "nothex", "0xdeadbeef", "0xzz"])
def test_decode_unknown_or_malformed(data: Any) -> None:
    assert decode_revert(data) is None


def test_decode_truncated_known_error_is_none() -> None:
    assert decode_revert(_selector("ProjectExists(bytes32)") + b"\x01") is None


def test_error_selectors_cover_all_contract_errors() -> None:
    names = {name for name, _, _ in error_selectors().values()}
    assert {"SelfApproval", "ExceedsApproved", "ReplacementOutstanding", "NotSeller"} <= names


def test_event_topic_matches_keccak_signature() -> None:
    entry = next(e for e in artifacts.abi("AquaRegistry") if e.get("name") == "ReportApproved")
    sig = "ReportApproved(uint256,bytes32,address,int256,uint256)"
    assert event_topic(entry) == "0x" + keccak(text=sig).hex()


# ---------------------------------------------------------------- status machine & policy


def _tx(status: ChainTxStatus) -> Any:
    return SimpleNamespace(id="t", status=status)


def test_allowed_transitions() -> None:
    tx = _tx(ChainTxStatus.QUEUED)
    transition(tx, ChainTxStatus.SUBMITTED)
    transition(tx, ChainTxStatus.CONFIRMED)
    assert tx.status is ChainTxStatus.CONFIRMED
    assert {ChainTxStatus.CONFIRMED, ChainTxStatus.FAILED, ChainTxStatus.REVERTED} == set(
        TERMINAL_STATUSES
    )


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (ChainTxStatus.QUEUED, ChainTxStatus.CONFIRMED),  # never confirmed without a broadcast
        (ChainTxStatus.QUEUED, ChainTxStatus.REVERTED),
        (ChainTxStatus.CONFIRMED, ChainTxStatus.FAILED),
        (ChainTxStatus.FAILED, ChainTxStatus.SUBMITTED),  # no resurrection
        (ChainTxStatus.REVERTED, ChainTxStatus.CONFIRMED),
        (ChainTxStatus.SUBMITTED, ChainTxStatus.QUEUED),
    ],
)
def test_forbidden_transitions(start: ChainTxStatus, target: ChainTxStatus) -> None:
    with pytest.raises(InvalidTransitionError):
        transition(_tx(start), target)


def test_every_status_has_transition_rules() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(ChainTxStatus)


def test_nonce_lock_key_is_per_sender_and_chain_and_case_insensitive() -> None:
    a = nonce_lock_key(31337, ANVIL_ADDRESSES[1])
    assert a == nonce_lock_key(31337, ANVIL_ADDRESSES[1].lower())
    assert a != nonce_lock_key(80002, ANVIL_ADDRESSES[1])
    assert a != nonce_lock_key(31337, ANVIL_ADDRESSES[2])
    assert -(2**63) <= a < 2**63


def test_policy_from_settings_converts_gwei() -> None:
    policy = RelayerPolicy.from_settings(
        Settings(_env_file=None, chain_gas_price_cap_gwei=50, chain_priority_fee_gwei=30)
    )
    assert policy.gas_price_cap_wei == 50 * 10**9
    assert policy.priority_fee_wei == 30 * 10**9


# ---------------------------------------------------------------- ABI export (offline)


def test_strip_metadata() -> None:
    code = "0x6080" + "a2" + "ff" * 2 + "0003"
    assert abi_export.strip_metadata(code) == "6080"  # 3 metadata bytes: a2ffff
    assert abi_export.strip_metadata("0x12") == "12"


def _fake_out(root: Path, abi: list[Any], code: str) -> Path:
    contracts = root / "contracts"
    for name in abi_export.CONTRACTS:
        d = contracts / "out" / f"{name}.sol"
        d.mkdir(parents=True)
        (d / f"{name}.json").write_text(json.dumps({"abi": abi, "bytecode": {"object": code}}))
    return contracts


def test_export_and_drift_detection(tmp_path: Path) -> None:
    contracts = _fake_out(tmp_path, [{"type": "function", "name": "f"}], "0x6080" + "aa0001")
    out = tmp_path / "abis"
    assert len(abi_export.export(contracts, out)) == len(abi_export.CONTRACTS)
    assert abi_export.drift(contracts, out) == []
    # metadata-only change is ignored; code change is not
    first = out / f"{abi_export.CONTRACTS[0]}.json"
    data = json.loads(first.read_text())
    data["bytecode"] = "0x6080" + "bb0001"
    first.write_text(json.dumps(data))
    assert abi_export.drift(contracts, out) == []
    data["bytecode"] = "0x6081" + "aa0001"
    data["abi"] = []
    first.write_text(json.dumps(data))
    second = out / f"{abi_export.CONTRACTS[1]}.json"
    data2 = json.loads(second.read_text())
    data2["bytecode"] = "0x6081" + "aa0001"
    second.write_text(json.dumps(data2))
    (out / f"{abi_export.CONTRACTS[2]}.json").unlink()
    problems = abi_export.drift(contracts, out)
    assert any("ABI differs" in p for p in problems)
    assert any("bytecode differs" in p for p in problems)
    assert any("missing" in p for p in problems)


def test_committed_abis_match_their_artifacts_layout() -> None:
    for name in abi_export.CONTRACTS:
        data = artifacts.load(name)
        assert data["contractName"] == name
        assert data["abi"]
        assert data["bytecode"].startswith("0x")


def test_funder_signer_is_separate_and_optional() -> None:
    s = Signers.from_settings(Settings(_env_file=None, relayer_mnemonic=ANVIL_DEV_MNEMONIC))
    assert s.address(ChainSigner.FUNDER) == s.derive(4).address
    assert len(set(s.addresses().values())) == 4
    with pytest.raises(ValueError, match="different keys"):
        _signers(funder_index=3)
    with pytest.raises(ChainNotConfiguredError, match="funder"):
        _signers().account(ChainSigner.FUNDER)


def test_waiting_for_gas_transitions() -> None:
    tx = _tx(ChainTxStatus.QUEUED)
    transition(tx, ChainTxStatus.WAITING_FOR_GAS)
    transition(tx, ChainTxStatus.SUBMITTED)
    assert tx.status is ChainTxStatus.SUBMITTED
    with pytest.raises(InvalidTransitionError):
        transition(tx, ChainTxStatus.WAITING_FOR_GAS)  # never after a broadcast
    parked = _tx(ChainTxStatus.WAITING_FOR_GAS)
    transition(parked, ChainTxStatus.FAILED)
    with pytest.raises(InvalidTransitionError):
        transition(_tx(ChainTxStatus.WAITING_FOR_GAS), ChainTxStatus.CONFIRMED)


def test_top_up_policy_from_settings() -> None:
    from aqua_chain.gas import TopUpPolicy

    policy = TopUpPolicy.from_settings(
        Settings(_env_file=None, chain_org_wallet_min_balance=5, chain_org_wallet_top_up=7)
    )
    assert (policy.min_balance, policy.amount) == (5, 7)
    assert policy.wallet_daily_cap == 6 * 10**16
    assert policy.global_daily_cap == 2 * 10**17
