// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {AquaCredit} from "./AquaCredit.sol";
import {AquaRegistry} from "./AquaRegistry.sol";

/// @title AquaForwardFund
/// @notice Forward funding of future blue-carbon removals through a reverse auction.
///
///         A buyer escrows `quantity × maxPrice` of the payment token and asks for future
///         long-term credits (lCCC, whole tCO2e). Registered projects offer a price per unit;
///         after the offer window, the cheapest offers win (ties: earliest offer) until the
///         quantity is covered. Escrow is released to a winner only as it delivers LongTerm
///         AquaCredit tokens of its own project before the delivery deadline. Whatever is not
///         delivered by the deadline is refundable to the buyer.
///
///         FUNDING IS NOT A GUARANTEE OF CREDITS. Credits are issued ex post, years after planting,
///         and only for removals a verifier approves; a project may deliver less than it offered,
///         or nothing. The optional upfront share (0–30 %) is paid to winners at award time to cover
///         planting costs AT THE BUYER'S RISK: it is never refunded, whether or not credits arrive.
///         TESTNET REGISTRY UNITS: the credits are not legally recognised carbon credits.
///
/// @dev Design notes (see contracts/README.md):
///      - Payments are pull-based (`claimable` + `withdraw`): no state change ever pushes tokens to
///        an arbitrary address, so a reverting recipient cannot block anyone else.
///      - Delivery: the contract, as an approved ERC-1155 operator of the project owner, moves the
///        credits straight from the project owner to the buyer with `safeTransferFrom`. It never
///        custodies credits and implements no `onERC1155Received`, so credits sent to it directly
///        are rejected by AquaCredit instead of being stuck. A buyer contract that refuses ERC-1155
///        can only block its own deliveries (it then gets the undelivered escrow back after the
///        deadline; the project keeps its credits and any upfront share).
///      - Bounded loops: at most `MAX_ACTIVE_OFFERS` active offers per request and one active offer
///        per project per request; `award` sorts at most that many offers.
///      - Payment token: a standard ERC-20 (MockUSDC on testnet). Fee-on-transfer or rebasing tokens
///        are not supported.
///      - Pausing stops new risk (requests, offers, deliveries). It never blocks `withdrawOffer`,
///        `award` (internal accounting over already-escrowed funds that frees the unused escrow),
///        `refundUndelivered` or `withdraw`, so funds already owed can always leave.
///      - Removal capacity from the latest MRV and stewardship eligibility (IMPLEMENTATION_PLAN
///        §Phase 7) are screened off-chain by the platform; on-chain, delivery of verified credits is
///        the only thing that releases escrow.
contract AquaForwardFund is AccessControl, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");

    /// @notice Upper bound on active offers per request (keeps `award` gas bounded).
    uint256 public constant MAX_ACTIVE_OFFERS = 100;
    /// @notice Upfront share cap: 30 % in basis points.
    uint16 public constant MAX_UPFRONT_BPS = 3000;
    uint16 internal constant BPS = 10_000;

    struct Request {
        address buyer;
        uint64 offerWindowEnd;
        uint64 deliveryDeadline;
        uint16 upfrontBps;
        bool awarded;
        uint256 quantity;
        uint256 maxPrice;
        /// Escrow still held for this request (not yet released, refunded or paid upfront).
        uint256 escrow;
        uint256 awardedQuantity;
    }

    struct Offer {
        uint256 requestId;
        bytes32 projectId;
        address seller;
        bool active;
        uint256 pricePerUnit;
        uint256 quantity;
    }

    /// @dev Keyed by the winning offer's id (awardId == offerId). Amounts fit in uint128 because
    ///      `createRequest` caps the escrow at type(uint128).max.
    struct Award {
        uint128 quantity;
        uint128 delivered;
        uint128 upfrontPaid;
        uint128 released;
        bool settled;
    }

    AquaRegistry public immutable registry;
    AquaCredit public immutable credit;
    IERC20 public immutable paymentToken;

    uint256 public requestCount;
    uint256 public offerCount;
    /// @notice Sum of all `claimable` balances.
    uint256 public totalClaimable;
    /// @notice Sum of all requests' `escrow`.
    uint256 public totalEscrow;

    mapping(uint256 requestId => Request) private _requests;
    mapping(uint256 offerId => Offer) private _offers;
    mapping(uint256 awardId => Award) private _awards;
    mapping(uint256 requestId => uint256[]) private _activeOffers;
    mapping(uint256 requestId => uint256[]) private _awardIds;
    mapping(uint256 offerId => uint256) private _activeIndex; // position + 1 in _activeOffers
    mapping(uint256 requestId => mapping(bytes32 projectId => uint256 offerId)) public activeOfferOf;
    /// @notice Pull-payment ledger: upfront shares, delivery releases and buyer refunds.
    mapping(address account => uint256) public claimable;

    event RequestCreated(
        uint256 indexed requestId,
        address indexed buyer,
        uint256 quantity,
        uint256 maxPrice,
        uint64 offerWindowEnd,
        uint64 deliveryDeadline,
        uint16 upfrontBps,
        uint256 escrow
    );
    event OfferSubmitted(
        uint256 indexed offerId,
        uint256 indexed requestId,
        bytes32 indexed projectId,
        address seller,
        uint256 pricePerUnit,
        uint256 quantity
    );
    event OfferWithdrawn(uint256 indexed offerId, uint256 indexed requestId);
    event OfferAwarded(
        uint256 indexed awardId,
        uint256 indexed requestId,
        bytes32 indexed projectId,
        address seller,
        uint256 quantity,
        uint256 pricePerUnit,
        uint256 upfrontPaid
    );
    event RequestAwarded(
        uint256 indexed requestId, uint256 awardedQuantity, uint256 awardedValue, uint256 unusedEscrowRefunded
    );
    event Delivered(
        uint256 indexed awardId, uint256 indexed tokenId, address indexed buyer, uint256 amount, uint256 released
    );
    event UndeliveredRefunded(uint256 indexed awardId, uint256 indexed requestId, uint256 undelivered, uint256 refund);
    event Withdrawn(address indexed account, uint256 amount);

    error InvalidInput();
    error UnknownRequest(uint256 requestId);
    error OfferWindowClosed(uint256 requestId);
    error OfferWindowOpen(uint256 requestId);
    error PriceAboveMax(uint256 price, uint256 maxPrice);
    error NotProjectOwner(bytes32 projectId);
    error DuplicateOffer(uint256 offerId);
    error TooManyOffers(uint256 requestId);
    error NotOfferSeller(uint256 offerId);
    error OfferInactive(uint256 offerId);
    error AlreadyAwarded(uint256 requestId);
    error NotAwardSeller(uint256 awardId);
    error DeadlinePassed(uint256 awardId);
    error DeadlineNotPassed(uint256 awardId);
    error ExceedsAward(uint256 remaining, uint256 amount);
    error NotLongTerm(uint256 tokenId);
    error WrongProject(uint256 tokenId, bytes32 expected, bytes32 actual);
    error CreditExpired(uint256 tokenId);
    error AlreadySettled(uint256 awardId);
    error NothingToWithdraw();

    constructor(address admin, AquaRegistry registry_, AquaCredit credit_, IERC20 paymentToken_) {
        if (
            admin == address(0) || address(registry_) == address(0) || address(credit_) == address(0)
                || address(paymentToken_) == address(0)
        ) revert InvalidInput();
        registry = registry_;
        credit = credit_;
        paymentToken = paymentToken_;
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(PAUSER_ROLE, admin);
    }

    // ------------------------------------------------------------------ buyer

    /// @notice Post a request for future long-term credits and escrow `quantity × maxPrice`.
    /// @param quantity future tCO2e wanted (whole credits).
    /// @param maxPrice highest acceptable price per credit, in payment-token base units.
    /// @param offerWindowEnd offers are accepted while block.timestamp < offerWindowEnd.
    /// @param deliveryDeadline deliveries are accepted while block.timestamp <= deliveryDeadline.
    /// @param upfrontBps share of each award paid at award time, 0–3000 bps. NON-REFUNDABLE: the buyer
    ///        bears the risk that no credits are ever delivered for it.
    function createRequest(
        uint256 quantity,
        uint256 maxPrice,
        uint64 offerWindowEnd,
        uint64 deliveryDeadline,
        uint16 upfrontBps
    ) external nonReentrant whenNotPaused returns (uint256 requestId) {
        // Timescales are months to years; validator timestamp drift is irrelevant.
        // forge-lint: disable-next-line(block-timestamp)
        if (quantity == 0 || maxPrice == 0 || offerWindowEnd <= block.timestamp) revert InvalidInput();
        if (deliveryDeadline <= offerWindowEnd || upfrontBps > MAX_UPFRONT_BPS) revert InvalidInput();
        uint256 escrow = quantity * maxPrice;
        if (escrow > type(uint128).max) revert InvalidInput();

        requestId = ++requestCount;
        _requests[requestId] = Request({
            buyer: msg.sender,
            offerWindowEnd: offerWindowEnd,
            deliveryDeadline: deliveryDeadline,
            upfrontBps: upfrontBps,
            awarded: false,
            quantity: quantity,
            maxPrice: maxPrice,
            escrow: escrow,
            awardedQuantity: 0
        });
        totalEscrow += escrow;
        emit RequestCreated(
            requestId, msg.sender, quantity, maxPrice, offerWindowEnd, deliveryDeadline, upfrontBps, escrow
        );
        paymentToken.safeTransferFrom(msg.sender, address(this), escrow);
    }

    // ------------------------------------------------------------------ projects

    /// @notice Offer to deliver `quantity` long-term credits of `projectId` at `pricePerUnit`.
    ///         Only the project's registered owner may offer, once per request while active.
    function submitOffer(uint256 requestId, bytes32 projectId, uint256 pricePerUnit, uint256 quantity)
        external
        nonReentrant
        whenNotPaused
        returns (uint256 offerId)
    {
        Request storage r = _requireRequest(requestId);
        // forge-lint: disable-next-line(block-timestamp)
        if (block.timestamp >= r.offerWindowEnd) revert OfferWindowClosed(requestId);
        if (pricePerUnit == 0 || quantity == 0 || quantity > r.quantity) revert InvalidInput();
        if (pricePerUnit > r.maxPrice) revert PriceAboveMax(pricePerUnit, r.maxPrice);
        // Reverts with AquaRegistry.UnknownProject for unregistered projects.
        if (registry.getProject(projectId).owner != msg.sender) revert NotProjectOwner(projectId);
        uint256 existing = activeOfferOf[requestId][projectId];
        if (existing != 0) revert DuplicateOffer(existing);
        if (_activeOffers[requestId].length >= MAX_ACTIVE_OFFERS) revert TooManyOffers(requestId);

        offerId = ++offerCount;
        _offers[offerId] = Offer({
            requestId: requestId,
            projectId: projectId,
            seller: msg.sender,
            active: true,
            pricePerUnit: pricePerUnit,
            quantity: quantity
        });
        _activeOffers[requestId].push(offerId);
        _activeIndex[offerId] = _activeOffers[requestId].length;
        activeOfferOf[requestId][projectId] = offerId;
        emit OfferSubmitted(offerId, requestId, projectId, msg.sender, pricePerUnit, quantity);
    }

    /// @notice Withdraw an offer before the offer window closes. Not pausable (it only reduces risk).
    function withdrawOffer(uint256 offerId) external {
        Offer storage o = _offers[offerId];
        if (o.seller != msg.sender) revert NotOfferSeller(offerId);
        if (!o.active) revert OfferInactive(offerId);
        uint256 requestId = o.requestId;
        // forge-lint: disable-next-line(block-timestamp)
        if (block.timestamp >= _requests[requestId].offerWindowEnd) revert OfferWindowClosed(requestId);

        o.active = false;
        delete activeOfferOf[requestId][o.projectId];
        uint256 idx = _activeIndex[offerId] - 1;
        uint256 last = _activeOffers[requestId][_activeOffers[requestId].length - 1];
        _activeOffers[requestId][idx] = last; // swap-and-pop; ties are broken by offer id, not position
        _activeIndex[last] = idx + 1;
        _activeOffers[requestId].pop();
        delete _activeIndex[offerId];
        emit OfferWithdrawn(offerId, requestId);
    }

    // ------------------------------------------------------------------ award

    /// @notice After the offer window, fill the request from the cheapest offers (ties: earliest).
    ///         Callable by anyone, once. The last winner may be partially filled. Unused escrow is
    ///         credited to the buyer immediately; each winner's upfront share is credited to it.
    /// @dev Not pausable: it only reallocates escrow already held and frees the buyer's unused part.
    ///      Insertion sort over at most MAX_ACTIVE_OFFERS offers.
    function award(uint256 requestId) external nonReentrant {
        Request storage r = _requireRequest(requestId);
        if (r.awarded) revert AlreadyAwarded(requestId);
        // forge-lint: disable-next-line(block-timestamp)
        if (block.timestamp < r.offerWindowEnd) revert OfferWindowOpen(requestId);
        r.awarded = true;

        uint256[] memory ids = _activeOffers[requestId];
        uint256 n = ids.length;
        uint256[] memory prices = new uint256[](n);
        for (uint256 i = 0; i < n; ++i) {
            uint256 id = ids[i];
            uint256 price = _offers[id].pricePerUnit;
            uint256 j = i;
            // Shift right while the previous entry ranks after (price, id).
            while (j > 0 && (prices[j - 1] > price || (prices[j - 1] == price && ids[j - 1] > id))) {
                prices[j] = prices[j - 1];
                ids[j] = ids[j - 1];
                --j;
            }
            prices[j] = price;
            ids[j] = id;
        }

        uint256 remaining = r.quantity;
        uint256 upfrontBps = r.upfrontBps;
        uint256 awardedValue = 0;
        uint256 upfrontTotal = 0;
        for (uint256 i = 0; i < n && remaining > 0; ++i) {
            Offer storage o = _offers[ids[i]];
            uint256 qty = Math.min(o.quantity, remaining);
            remaining -= qty;
            uint256 value = qty * prices[i];
            uint256 upfront = (value * upfrontBps) / BPS;
            awardedValue += value;
            upfrontTotal += upfront;
            claimable[o.seller] += upfront;
            // Safe: value ≤ escrow ≤ type(uint128).max (enforced in createRequest).
            _awards[ids[i]] = Award({
                // forge-lint: disable-next-line(unsafe-typecast)
                quantity: uint128(qty),
                delivered: 0,
                // forge-lint: disable-next-line(unsafe-typecast)
                upfrontPaid: uint128(upfront),
                released: 0,
                settled: false
            });
            _awardIds[requestId].push(ids[i]);
            emit OfferAwarded(ids[i], requestId, o.projectId, o.seller, qty, prices[i], upfront);
        }

        uint256 unused = r.escrow - awardedValue;
        address buyer = r.buyer;
        r.awardedQuantity = r.quantity - remaining;
        r.escrow = awardedValue - upfrontTotal;
        totalEscrow -= unused + upfrontTotal;
        claimable[buyer] += unused;
        totalClaimable += unused + upfrontTotal;
        emit RequestAwarded(requestId, r.awardedQuantity, awardedValue, unused);
    }

    // ------------------------------------------------------------------ delivery

    /// @notice Deliver `amount` LongTerm credits of the awarded project to the buyer and earn the
    ///         matching escrow. The caller must be the winning project owner and must have approved
    ///         this contract with `credit.setApprovalForAll`. Credits go straight to the buyer.
    /// @dev Release is cumulative and exact: after delivering d of q units the project has been
    ///      released (q·price − upfront)·d/q, so the full delivery releases exactly the rest of the
    ///      award and no dust is left. The upfront share is therefore amortised over the units.
    function deliver(uint256 awardId, uint256 tokenId, uint256 amount) external nonReentrant whenNotPaused {
        Offer storage o = _offers[awardId];
        Award storage a = _awards[awardId];
        if (a.quantity == 0 || o.seller != msg.sender) revert NotAwardSeller(awardId);
        Request storage r = _requests[o.requestId];
        // forge-lint: disable-next-line(block-timestamp)
        if (block.timestamp > r.deliveryDeadline) revert DeadlinePassed(awardId);
        uint256 open = a.quantity - a.delivered;
        if (amount == 0 || amount > open) revert ExceedsAward(open, amount);

        AquaCredit.Vintage memory v = credit.vintage(tokenId);
        if (!v.exists || v.creditType != AquaCredit.CreditType.LongTerm) revert NotLongTerm(tokenId);
        if (v.projectId != o.projectId) revert WrongProject(tokenId, o.projectId, v.projectId);
        // forge-lint: disable-next-line(block-timestamp)
        if (block.timestamp >= v.expiresAt) revert CreditExpired(tokenId);

        uint256 delivered = a.delivered + amount;
        uint256 total = uint256(a.quantity) * o.pricePerUnit - a.upfrontPaid;
        uint256 releasedNow = Math.mulDiv(total, delivered, a.quantity);
        uint256 payout = releasedNow - a.released;
        // Safe: delivered ≤ quantity and releasedNow ≤ total, both < 2^128.
        // forge-lint: disable-next-line(unsafe-typecast)
        a.delivered = uint128(delivered);
        // forge-lint: disable-next-line(unsafe-typecast)
        a.released = uint128(releasedNow);
        r.escrow -= payout;
        totalEscrow -= payout;
        claimable[msg.sender] += payout;
        totalClaimable += payout;
        address buyer = r.buyer;
        emit Delivered(awardId, tokenId, buyer, amount, payout);
        credit.safeTransferFrom(msg.sender, buyer, tokenId, amount, "");
    }

    // ------------------------------------------------------------------ refunds and claims

    /// @notice After the delivery deadline, credit the buyer with the escrow left on an award (the
    ///         undelivered units). Callable by anyone, once per award. Not pausable. The upfront
    ///         share already paid is NOT recoverable: it was advanced at the buyer's risk.
    function refundUndelivered(uint256 awardId) external {
        Award storage a = _awards[awardId];
        if (a.quantity == 0) revert NotAwardSeller(awardId);
        if (a.settled) revert AlreadySettled(awardId);
        Offer storage o = _offers[awardId];
        Request storage r = _requests[o.requestId];
        // forge-lint: disable-next-line(block-timestamp)
        if (block.timestamp <= r.deliveryDeadline) revert DeadlineNotPassed(awardId);

        a.settled = true;
        uint256 refund = uint256(a.quantity) * o.pricePerUnit - a.upfrontPaid - a.released;
        r.escrow -= refund;
        totalEscrow -= refund;
        claimable[r.buyer] += refund;
        totalClaimable += refund;
        emit UndeliveredRefunded(awardId, o.requestId, a.quantity - a.delivered, refund);
    }

    /// @notice Withdraw everything owed to the caller (refunds, upfront shares, delivery releases).
    ///         Not pausable: earned funds can always leave.
    function withdraw() external nonReentrant {
        uint256 amount = claimable[msg.sender];
        if (amount == 0) revert NothingToWithdraw();
        claimable[msg.sender] = 0;
        totalClaimable -= amount;
        emit Withdrawn(msg.sender, amount);
        paymentToken.safeTransfer(msg.sender, amount);
    }

    // ------------------------------------------------------------------ admin

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(PAUSER_ROLE) {
        _unpause();
    }

    // ------------------------------------------------------------------ views

    function getRequest(uint256 requestId) external view returns (Request memory) {
        return _requests[requestId];
    }

    function getOffer(uint256 offerId) external view returns (Offer memory) {
        return _offers[offerId];
    }

    function getAward(uint256 awardId) external view returns (Award memory) {
        return _awards[awardId];
    }

    function activeOffers(uint256 requestId) external view returns (uint256[] memory) {
        return _activeOffers[requestId];
    }

    function awardIds(uint256 requestId) external view returns (uint256[] memory) {
        return _awardIds[requestId];
    }

    function _requireRequest(uint256 requestId) private view returns (Request storage r) {
        r = _requests[requestId];
        if (r.buyer == address(0)) revert UnknownRequest(requestId);
    }
}
