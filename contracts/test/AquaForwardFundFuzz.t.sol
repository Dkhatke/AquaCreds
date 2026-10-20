// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {AquaForwardFund} from "../src/AquaForwardFund.sol";
import {FundBase} from "./FundBase.t.sol";

/// Random offer sets: award bounds, price ordering and exact conservation of the escrow.
contract AquaForwardFundFuzzTest is FundBase {
    uint256 internal constant N = 12;
    uint256[N] internal tokenIds;

    function setUp() public override {
        super.setUp();
        for (uint256 i = 0; i < N; ++i) {
            bytes32 id = _registerProject(i);
            tokenIds[i] = _issueLongFor(id, _owner(i), 1_000_000);
            vm.prank(_owner(i));
            credit.setApprovalForAll(address(fund), true);
        }
    }

    function _sumClaimable() internal returns (uint256 projects) {
        for (uint256 i = 0; i < N; ++i) {
            projects += fund.claimable(_owner(i));
        }
    }

    // Per-run state kept in storage (avoids stack-too-deep).
    uint256 internal req;
    uint256 internal original;
    uint256 internal bps;
    uint256[] internal offerIds;
    uint256[] internal prices;
    uint256[] internal qtys;
    uint256[] internal delivered;

    function testFuzz_AwardSettleConservesEscrow(
        uint256 quantity,
        uint256 maxPrice,
        uint16 bps_,
        uint8 n,
        uint256[N] memory priceSeeds,
        uint256[N] memory qtySeeds,
        uint256[N] memory deliverySeeds
    ) public {
        quantity = bound(quantity, 1, 5_000);
        maxPrice = bound(maxPrice, 1, 1e12);
        bps = bound(bps_, 0, fund.MAX_UPFRONT_BPS());
        n = uint8(bound(n, 0, N));
        original = quantity * maxPrice;
        // forge-lint: disable-next-line(unsafe-typecast)
        req = _createRequest(quantity, maxPrice, uint16(bps));
        for (uint256 i = 0; i < n; ++i) {
            prices.push(bound(priceSeeds[i], 1, maxPrice));
            qtys.push(bound(qtySeeds[i], 1, quantity));
            offerIds.push(_offer(req, i, prices[i], qtys[i]));
        }
        vm.warp(windowEnd);
        fund.award(req);
        _checkAward(quantity);
        _deliverRandomly(deliverySeeds);
        _settleAndCheck();
    }

    function _checkAward(uint256 quantity) internal {
        AquaForwardFund.Request memory r = fund.getRequest(req);
        assertLe(r.awardedQuantity, quantity, "over-awarded");
        uint256 sumQty;
        uint256 maxWinPrice;
        uint256 minLosePrice = type(uint256).max;
        for (uint256 i = 0; i < offerIds.length; ++i) {
            uint256 q = fund.getAward(offerIds[i]).quantity;
            sumQty += q;
            assertLe(q, qtys[i]);
            if (q > 0 && prices[i] > maxWinPrice) maxWinPrice = prices[i];
            if (q == 0 && prices[i] < minLosePrice) minLosePrice = prices[i];
        }
        assertEq(sumQty, r.awardedQuantity);
        assertLe(maxWinPrice, minLosePrice, "a cheaper offer lost");
        // Either the request is fully covered or every offer won.
        if (r.awardedQuantity < quantity) assertEq(minLosePrice, type(uint256).max);
        // buyer refund + upfront payouts + remaining escrow == original escrow
        assertEq(fund.claimable(buyer) + _sumClaimable() + r.escrow, original, "award leak");
        _assertSolvent();
    }

    function _deliverRandomly(uint256[N] memory seeds) internal {
        for (uint256 i = 0; i < offerIds.length; ++i) {
            uint256 q = fund.getAward(offerIds[i]).quantity;
            uint256 d = q == 0 ? 0 : bound(seeds[i], 0, q);
            delivered.push(d);
            if (d == 0) continue;
            vm.prank(_owner(i));
            fund.deliver(offerIds[i], tokenIds[i], d);
            assertEq(fund.claimable(buyer) + _sumClaimable() + fund.getRequest(req).escrow, original, "delivery leak");
        }
    }

    function _settleAndCheck() internal {
        vm.warp(uint256(deadline) + 1);
        for (uint256 i = 0; i < offerIds.length; ++i) {
            if (fund.getAward(offerIds[i]).quantity > 0) fund.refundUndelivered(offerIds[i]);
        }
        assertEq(fund.getRequest(req).escrow, 0, "escrow left after settlement");
        assertEq(fund.claimable(buyer) + _sumClaimable(), original, "settlement leak");
        for (uint256 i = 0; i < offerIds.length; ++i) {
            AquaForwardFund.Award memory a = fund.getAward(offerIds[i]);
            // Paid <= upfront share + delivered value: never paid for undelivered credits.
            assertLe(fund.claimable(_owner(i)), a.upfrontPaid + delivered[i] * prices[i], "paid for undelivered");
            assertEq(a.upfrontPaid, (uint256(a.quantity) * prices[i] * bps) / 10_000);
            assertEq(credit.balanceOf(buyer, tokenIds[i]), delivered[i]);
        }
        _assertSolvent();
    }
}
