# AquaCreds smart contracts

Foundry project (Solidity 0.8.30, OpenZeppelin 5.7.0). **Testnet registry units**: these tokens
are not legally recognised carbon credits.

| Contract | Role |
|---|---|
| `AquaRegistry` | Projects (boundary hash, owner) and monitoring reports (report hash, evidence URI, approved lCCC/tCCC). Separation of duties: the submitter can't approve its own report. Periods must be contiguous; one pending report per project; reversals (lCCC < 0) create a replacement obligation. |
| `AquaCredit` (ERC-1155) | One token id per (report, credit type): Temporary (tCCC, expiring) or LongTerm (lCCC). Issuance capped by the approved quantities; blocked while a reversal is unsettled. `retire()` burns and emits a certificate event; expired credits can't move. |
| `AquaMarketplace` | Non-custodial fixed-price listings paid in an ERC-20; pull-payment proceeds; fee ≤ 10%; buyer slippage guard. |
| `MockUSDC` | Testnet 6-decimal payment token with a daily faucet. |

## Commands
`./forge.sh` uses a local Foundry install if present, otherwise the pinned Docker image
(`ghcr.io/foundry-rs/foundry:v1.7.0`, removed after each run).

```bash
./forge.sh soldeer install          # dependencies → contracts/dependencies (git-ignored)
./forge.sh build
./forge.sh test -vv                 # unit + fuzz (1,000 runs) + invariants (8,192 calls)
./forge.sh coverage --no-match-coverage "(test|script)/"
./forge.sh script script/Deploy.s.sol    # local simulation; see the script for Amoy flags
```

## Guarantees checked by tests
- Invariant: for every report, issued ≤ approved, and supply + retired ≤ issued (random call sequences).
- Invariant: no issuance while a replacement obligation is outstanding.
- Fuzz: no combination of issue amounts exceeds the approved lCCC.
- 48 tests; 100% line, statement, branch and function coverage of `src/`.
