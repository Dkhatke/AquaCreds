# AquaCreds smart contracts

Foundry project (Solidity 0.8.30, OpenZeppelin 5.7.0). **Testnet registry units**: these tokens
are not legally recognised carbon credits.

| Contract | Role |
|---|---|
| `AquaRegistry` | Projects (boundary hash, owner) and monitoring reports (report hash, evidence URI, approved lCCC/tCCC). Separation of duties: the submitter can't approve its own report. Periods must be contiguous; one pending report per project; reversals (lCCC < 0) create a replacement obligation. |
| `AquaCredit` (ERC-1155) | One token id per (report, credit type): Temporary (tCCC, expiring) or LongTerm (lCCC). Issuance capped by the approved quantities; blocked while a reversal is unsettled. `retire()` burns and emits a certificate event; expired credits can't move. |
| `AquaMarketplace` | Non-custodial fixed-price listings paid in an ERC-20; pull-payment proceeds; fee ≤ 10%; buyer slippage guard. |
| `MockUSDC` | Testnet 6-decimal payment token with a daily faucet. |
| `AquaForwardFund` | Forward funding by reverse auction (IMPLEMENTATION_PLAN §Phase 7): buyers escrow funds for future lCCC, registered projects bid a price, the cheapest win, and escrow is released only as LongTerm credits are delivered. See below. |

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
- Invariant (forward fund): the contract's payment-token balance equals outstanding obligations (every
  request's remaining escrow + every claimable balance, summed independently); no buyer gets back more
  than it deposited; no project is paid more than its upfront share plus the value of credits it
  delivered; awarded quantity ≤ requested.
- Fuzz (forward fund): random offer sets → awarded ≤ requested, no cheaper offer loses, and payouts +
  refunds + remaining escrow == the original escrow after award, after each delivery and after settlement.
- 93 tests; 100% line, statement, branch and function coverage of `src/`.

## AquaForwardFund (forward funding by reverse auction)

> **Funding is not a guarantee of credits.** Credits are issued ex post, years after planting (BM
> FR05.001 verification periods; see RESEARCH_NOTES Q3), and only for removals a verifier approves. A
> project may deliver less than it offered, or nothing. The optional **upfront share is paid at the
> buyer's risk and is never refunded.** Any UI must show this explicitly.

1. **Request** — `createRequest(quantity, maxPrice, offerWindowEnd, deliveryDeadline, upfrontBps)`: the
   buyer escrows `quantity × maxPrice` (payment token, e.g. mUSDC with 6 decimals). `upfrontBps` is
   0–3000 (0–30 %). Escrow is capped at 2^128 − 1 base units.
2. **Offers** — `submitOffer(requestId, projectId, pricePerUnit, quantity)` while
   `block.timestamp < offerWindowEnd`. The project must exist in `AquaRegistry` and the caller must be
   its registered owner; price ≤ max; quantity ≤ requested. One active offer per project per request,
   at most **100 active offers per request** (`MAX_ACTIVE_OFFERS`), so `award` gas is bounded (worst
   case measured: 100 winners in reverse order ≈ 10.1 M gas). `withdrawOffer` before the window closes.
3. **Award** — `award(requestId)`, by anyone, once, after the window: offers sorted by price, ties by
   earliest offer id; filled until the quantity is covered (the last winner may be partially filled).
   Unused escrow is credited to the buyer at once; each winner's upfront share
   (`floor(qty × price × bps / 10 000)`) is credited to its owner. Award id == winning offer id.
4. **Delivery** — `deliver(awardId, tokenId, amount)` by the winning owner, while
   `block.timestamp ≤ deliveryDeadline`. The token must be a **LongTerm** vintage of **the awarded
   project** and not expired. The owner first calls `credit.setApprovalForAll(fund, true)`; the fund then
   moves the credits **straight from the owner to the buyer** (it never custodies credits). The release
   is cumulative and exact: after `d` of `q` units the project has received
   `(q × price − upfront) × d / q`, so a full delivery releases exactly the rest (no dust).
5. **Refund** — after the deadline, `refundUndelivered(awardId)` (anyone, once per award) credits the
   buyer with the award's unreleased escrow. Upfront shares are not recovered.
6. **Claims** — every payout is pull-based: `withdraw()` sends the caller its `claimable` balance.

**Design choices.**
- *Delivery by operator pull, not `onERC1155Received`:* no custody, no receiver hook to secure, atomic
  validation + transfer, and credits sent to the fund directly are rejected by AquaCredit (no stuck
  tokens). A buyer contract that refuses ERC-1155 only blocks its own deliveries: it then recovers the
  undelivered escrow after the deadline, and the project keeps its credits and upfront share.
- *Pause* (`PAUSER_ROLE`) stops new risk: `createRequest`, `submitOffer`, `deliver`. It never blocks
  `withdrawOffer`, `award` (it only reallocates escrow already held and frees the buyer's unused part),
  `refundUndelivered` or `withdraw`, so funds already owed can always leave. A pause during the run-up to
  a deadline also blocks deliveries, so admins should unpause before deadlines (deadlines are not extended).
- *Off-chain screening:* the plan's "subject to estimated removal capacity from the latest MRV" and
  stewardship-score eligibility are checked by the platform before an organisation's relayer submits an
  offer; on-chain, only delivered, verified LongTerm credits release escrow.
- Standard ERC-20 payment tokens only (no fee-on-transfer/rebasing). ReentrancyGuard on every function
  that moves tokens; checks-effects-interactions; SafeERC20; custom errors; an event for every state change.
- Slither: no medium/high findings; the only low finding is `timestamp` (windows and deadlines are
  months/years long, so validator drift is irrelevant).
