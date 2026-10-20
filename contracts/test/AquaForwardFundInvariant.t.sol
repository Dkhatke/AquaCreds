// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Test, console} from "forge-std/Test.sol";
import {AquaForwardFund} from "../src/AquaForwardFund.sol";
import {MockUSDC} from "../src/MockUSDC.sol";
import {FundBase} from "./FundBase.t.sol";

/// Random create / offer / withdraw / award / deliver / refund / claim sequences with time jumps.
contract FundHandler is Test {
    uint256 internal constant PROJECTS = 5;
    uint256 internal constant BUYERS = 3;

    AquaForwardFund internal fund;
    MockUSDC internal usdc;
    address[BUYERS] public buyers;
    address[PROJECTS] public owners;
    bytes32[PROJECTS] internal projectIds;
    uint256[PROJECTS] internal tokenIds;

    uint256[] public requests;
    uint256[] public offers;
    uint256[] public awards;

    // Ghost accounting, independent of the contract's own counters.
    mapping(address => uint256) public deposited;
    mapping(address => uint256) public withdrawn;
    /// Upper bound on what a project may ever be paid: upfront shares + value of delivered credits.
    mapping(address => uint256) public earnedCap;
    mapping(bytes4 => uint256) public calls;

    constructor(
        AquaForwardFund fund_,
        MockUSDC usdc_,
        address[PROJECTS] memory owners_,
        bytes32[PROJECTS] memory projectIds_,
        uint256[PROJECTS] memory tokenIds_
    ) {
        fund = fund_;
        usdc = usdc_;
        owners = owners_;
        projectIds = projectIds_;
        tokenIds = tokenIds_;
        for (uint256 i = 0; i < BUYERS; ++i) {
            buyers[i] = makeAddr(string(abi.encodePacked("fuzz-buyer", vm.toString(i))));
            vm.prank(buyers[i]);
            usdc.approve(address(fund), type(uint256).max);
        }
    }

    /// Picks one of the 6 most recent entries, so actions mostly target live auctions.
    function _recent(uint256[] storage list, uint256 seed) internal view returns (uint256) {
        uint256 window = list.length < 6 ? list.length : 6;
        return list[list.length - 1 - (seed % window)];
    }

    function requestCount() external view returns (uint256) {
        return requests.length;
    }

    function awardCount() external view returns (uint256) {
        return awards.length;
    }

    function createRequest(
        uint256 buyerSeed,
        uint256 quantity,
        uint256 price,
        uint256 bps,
        uint256 window,
        uint256 span
    ) external {
        address b = buyers[buyerSeed % BUYERS];
        quantity = bound(quantity, 1, 2_000);
        price = bound(price, 1, 1e9);
        uint16 upfront = uint16(bound(bps, 0, 3000));
        uint64 end = uint64(block.timestamp + bound(window, 1 days, 30 days));
        uint64 dl = end + uint64(bound(span, 7 days, 90 days));
        usdc.mint(b, quantity * price); // the handler owns MockUSDC
        vm.prank(b);
        uint256 id = fund.createRequest(quantity, price, end, dl, upfront);
        requests.push(id);
        deposited[b] += quantity * price;
        calls[this.createRequest.selector]++;
    }

    function submitOffer(uint256 reqSeed, uint256 projSeed, uint256 price, uint256 quantity) external {
        if (requests.length == 0) return;
        uint256 req = _recent(requests, reqSeed);
        AquaForwardFund.Request memory r = fund.getRequest(req);
        uint256 p = projSeed % PROJECTS;
        price = bound(price, 1, r.maxPrice);
        quantity = bound(quantity, 1, r.quantity);
        vm.prank(owners[p]);
        try fund.submitOffer(req, projectIds[p], price, quantity) returns (uint256 id) {
            offers.push(id);
            calls[this.submitOffer.selector]++;
        } catch {}
    }

    function withdrawOffer(uint256 offerSeed) external {
        if (offers.length == 0) return;
        uint256 id = _recent(offers, offerSeed);
        vm.prank(fund.getOffer(id).seller);
        try fund.withdrawOffer(id) {
            calls[this.withdrawOffer.selector]++;
        } catch {}
    }

    function award(uint256 reqSeed) external {
        if (requests.length == 0) return;
        uint256 req = _recent(requests, reqSeed);
        try fund.award(req) {
            uint256 bps = fund.getRequest(req).upfrontBps;
            uint256[] memory ids = fund.awardIds(req);
            for (uint256 i = 0; i < ids.length; ++i) {
                AquaForwardFund.Offer memory o = fund.getOffer(ids[i]);
                uint256 q = fund.getAward(ids[i]).quantity;
                earnedCap[o.seller] += (q * o.pricePerUnit * bps) / 10_000;
                awards.push(ids[i]);
            }
            calls[this.award.selector]++;
        } catch {}
    }

    function deliver(uint256 awardSeed, uint256 amount, uint256 mode) external {
        if (awards.length == 0) return;
        uint256 id = _recent(awards, awardSeed);
        AquaForwardFund.Offer memory o = fund.getOffer(id);
        uint256 p;
        while (owners[p] != o.seller) ++p;
        AquaForwardFund.Award memory a = fund.getAward(id);
        amount = bound(amount, 1, a.quantity - a.delivered + 1); // sometimes one too many
        bool wrongToken = mode % 8 == 0; // sometimes another project's credits
        uint256 tokenId = wrongToken ? tokenIds[(p + 1) % PROJECTS] : tokenIds[p];
        vm.prank(o.seller);
        try fund.deliver(id, tokenId, amount) {
            assertFalse(wrongToken, "delivered another project's credits");
            earnedCap[o.seller] += amount * o.pricePerUnit;
            calls[this.deliver.selector]++;
        } catch {}
    }

    function refund(uint256 awardSeed) external {
        if (awards.length == 0) return;
        try fund.refundUndelivered(_recent(awards, awardSeed)) {
            calls[this.refund.selector]++;
        } catch {}
    }

    function claim(uint256 actorSeed) external {
        uint256 k = actorSeed % (BUYERS + PROJECTS);
        address who = k < BUYERS ? buyers[k] : owners[k - BUYERS];
        uint256 amount = fund.claimable(who);
        vm.prank(who);
        try fund.withdraw() {
            withdrawn[who] += amount;
            calls[this.claim.selector]++;
        } catch {}
    }

    function warp(uint256 secs) external {
        vm.warp(block.timestamp + bound(secs, 1 hours, 15 days));
        calls[this.warp.selector]++;
    }
}

contract ForwardFundInvariants is FundBase {
    FundHandler internal handler;

    function setUp() public override {
        super.setUp();
        address[5] memory owners;
        bytes32[5] memory ids;
        uint256[5] memory tokens;
        for (uint256 i = 0; i < 5; ++i) {
            ids[i] = _registerProject(i);
            owners[i] = _owner(i);
            tokens[i] = _issueLongFor(ids[i], owners[i], 1e9);
            vm.prank(owners[i]);
            credit.setApprovalForAll(address(fund), true);
        }
        // The handler mints mock USDC for buyers.
        vm.prank(admin);
        usdc.transferOwnership(address(this));
        handler = new FundHandler(fund, usdc, owners, ids, tokens);
        usdc.transferOwnership(address(handler));
        targetContract(address(handler));
    }

    /// The contract's token balance equals its outstanding obligations, computed independently:
    /// every request's remaining escrow plus every account's claimable balance.
    function invariant_BalanceEqualsObligations() public view {
        uint256 escrow;
        uint256 n = handler.requestCount();
        for (uint256 i = 0; i < n; ++i) {
            escrow += fund.getRequest(handler.requests(i)).escrow;
        }
        uint256 owed;
        for (uint256 i = 0; i < 3; ++i) {
            owed += fund.claimable(handler.buyers(i));
        }
        for (uint256 i = 0; i < 5; ++i) {
            owed += fund.claimable(handler.owners(i));
        }
        uint256 balance = usdc.balanceOf(address(fund));
        assertEq(balance, escrow + owed, "balance != obligations");
        assertEq(escrow, fund.totalEscrow());
        assertEq(owed, fund.totalClaimable());
    }

    /// No buyer ever gets back more than it deposited.
    function invariant_BuyerNeverOverRefunded() public view {
        for (uint256 i = 0; i < 3; ++i) {
            address b = handler.buyers(i);
            assertLe(handler.withdrawn(b) + fund.claimable(b), handler.deposited(b), "buyer over-refunded");
        }
    }

    /// No project is paid beyond its upfront shares plus the value of credits it delivered.
    function invariant_ProjectPaidOnlyForDeliveries() public view {
        for (uint256 i = 0; i < 5; ++i) {
            address o = handler.owners(i);
            assertLe(handler.withdrawn(o) + fund.claimable(o), handler.earnedCap(o), "project overpaid");
        }
        uint256 n = handler.awardCount();
        for (uint256 i = 0; i < n; ++i) {
            uint256 id = handler.awards(i);
            AquaForwardFund.Award memory a = fund.getAward(id);
            uint256 price = fund.getOffer(id).pricePerUnit;
            assertLe(a.delivered, a.quantity);
            assertLe(a.released, uint256(a.delivered) * price);
        }
    }

    /// Awarded quantity never exceeds what the buyer asked for.
    function invariant_AwardedWithinRequest() public view {
        uint256 n = handler.requestCount();
        for (uint256 i = 0; i < n; ++i) {
            AquaForwardFund.Request memory r = fund.getRequest(handler.requests(i));
            assertLe(r.awardedQuantity, r.quantity);
        }
    }

    /// A long deterministic pseudo-random walk through the handler, checking every invariant as it
    /// goes; logs how many of each call succeeded (evidence that deliveries and refunds are reached).
    function test_LongRandomWalk() public {
        for (uint256 step = 0; step < 2_000; ++step) {
            uint256 r = uint256(keccak256(abi.encode(step)));
            uint256 op = r % 8;
            uint256 a = r >> 8;
            uint256 b = r >> 64;
            uint256 c = r >> 128;
            if (op == 0) handler.createRequest(a, b, c, a >> 40, b >> 40, c >> 40);
            else if (op == 1) handler.submitOffer(a, b, c, a >> 30);
            else if (op == 2) handler.withdrawOffer(a);
            else if (op == 3) handler.award(a);
            else if (op == 4) handler.deliver(a, b, c);
            else if (op == 5) handler.refund(a);
            else if (op == 6) handler.claim(a);
            else handler.warp(a);
            if (step % 50 == 49) {
                invariant_BalanceEqualsObligations();
                invariant_BuyerNeverOverRefunded();
                invariant_ProjectPaidOnlyForDeliveries();
                invariant_AwardedWithinRequest();
            }
        }
        this.afterInvariant();
        assertGt(handler.calls(FundHandler.deliver.selector), 0, "no deliveries reached");
        assertGt(handler.calls(FundHandler.refund.selector), 0, "no refunds reached");
    }

    function afterInvariant() external view {
        // Coverage of the handler's paths (logged with -vv).
        bytes4[8] memory sels = [
            FundHandler.createRequest.selector,
            FundHandler.submitOffer.selector,
            FundHandler.withdrawOffer.selector,
            FundHandler.award.selector,
            FundHandler.deliver.selector,
            FundHandler.refund.selector,
            FundHandler.claim.selector,
            FundHandler.warp.selector
        ];
        string[8] memory names =
            ["createRequest", "submitOffer", "withdrawOffer", "award", "deliver", "refund", "claim", "warp"];
        for (uint256 i = 0; i < 8; ++i) {
            console.log(names[i], handler.calls(sels[i]));
        }
    }
}
