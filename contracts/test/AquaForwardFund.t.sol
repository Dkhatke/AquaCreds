// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {IERC1155Errors} from "@openzeppelin/contracts/interfaces/draft-IERC6093.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {AquaCredit} from "../src/AquaCredit.sol";
import {AquaForwardFund} from "../src/AquaForwardFund.sol";
import {AquaRegistry} from "../src/AquaRegistry.sol";
import {FundBase} from "./FundBase.t.sol";

contract AquaForwardFundTest is FundBase {
    uint256 internal constant MAX = 15e6;

    function setUp() public override {
        super.setUp();
        for (uint256 i = 0; i < 4; ++i) {
            _registerProject(i);
        }
    }

    // ------------------------------------------------------------------ construction / admin

    function test_ConstructorRejectsZeroAddresses() public {
        vm.expectRevert(AquaForwardFund.InvalidInput.selector);
        new AquaForwardFund(address(0), registry, credit, usdc);
        vm.expectRevert(AquaForwardFund.InvalidInput.selector);
        new AquaForwardFund(admin, AquaRegistry(address(0)), credit, usdc);
        vm.expectRevert(AquaForwardFund.InvalidInput.selector);
        new AquaForwardFund(admin, registry, AquaCredit(address(0)), usdc);
        vm.expectRevert(AquaForwardFund.InvalidInput.selector);
        new AquaForwardFund(admin, registry, credit, IERC20(address(0)));
    }

    function test_OnlyPauserPauses() public {
        bytes32 role = fund.PAUSER_ROLE();
        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, buyer, role));
        fund.pause();
        vm.prank(admin);
        fund.pause();
        assertTrue(fund.paused());
        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, buyer, role));
        fund.unpause();
        vm.prank(admin);
        fund.unpause();
        assertFalse(fund.paused());
    }

    // ------------------------------------------------------------------ createRequest

    function test_CreateRequestEscrowsFunds() public {
        _fundBuyer(buyer, 100 * MAX);
        vm.expectEmit(address(fund));
        emit AquaForwardFund.RequestCreated(1, buyer, 100, MAX, windowEnd, deadline, 2000, 100 * MAX);
        vm.prank(buyer);
        uint256 id = fund.createRequest(100, MAX, windowEnd, deadline, 2000);
        assertEq(id, 1);
        assertEq(usdc.balanceOf(address(fund)), 100 * MAX);
        assertEq(usdc.balanceOf(buyer), 0);
        AquaForwardFund.Request memory r = fund.getRequest(id);
        assertEq(r.buyer, buyer);
        assertEq(r.quantity, 100);
        assertEq(r.maxPrice, MAX);
        assertEq(r.escrow, 100 * MAX);
        assertEq(r.upfrontBps, 2000);
        assertFalse(r.awarded);
        assertEq(fund.totalEscrow(), 100 * MAX);
        _assertSolvent();
    }

    function test_RevertWhen_CreateRequestInvalid() public {
        _fundBuyer(buyer, type(uint128).max);
        uint64 nowTs = uint64(block.timestamp);
        vm.startPrank(buyer);
        vm.expectRevert(AquaForwardFund.InvalidInput.selector);
        fund.createRequest(0, MAX, windowEnd, deadline, 0);
        vm.expectRevert(AquaForwardFund.InvalidInput.selector);
        fund.createRequest(10, 0, windowEnd, deadline, 0);
        vm.expectRevert(AquaForwardFund.InvalidInput.selector);
        fund.createRequest(10, MAX, nowTs, deadline, 0);
        vm.expectRevert(AquaForwardFund.InvalidInput.selector);
        fund.createRequest(10, MAX, windowEnd, windowEnd, 0);
        vm.expectRevert(AquaForwardFund.InvalidInput.selector);
        fund.createRequest(10, MAX, windowEnd, deadline, 3001);
        vm.expectRevert(AquaForwardFund.InvalidInput.selector);
        fund.createRequest(uint256(type(uint128).max) + 1, 1, windowEnd, deadline, 0);
        // 30 % exactly and the uint128 bound exactly are accepted.
        fund.createRequest(type(uint128).max, 1, windowEnd, deadline, 3000);
        vm.stopPrank();
    }

    function test_RevertWhen_CreateRequestWithoutFunds() public {
        vm.prank(buyer);
        vm.expectRevert(); // ERC20InsufficientAllowance
        fund.createRequest(10, MAX, windowEnd, deadline, 0);
    }

    // ------------------------------------------------------------------ offers

    function test_SubmitOffer() public {
        uint256 req = _createRequest(100, MAX, 0);
        vm.expectEmit(address(fund));
        emit AquaForwardFund.OfferSubmitted(1, req, _projectId(0), _owner(0), 10e6, 60);
        uint256 id = _offer(req, 0, 10e6, 60);
        AquaForwardFund.Offer memory o = fund.getOffer(id);
        assertEq(o.requestId, req);
        assertEq(o.projectId, _projectId(0));
        assertEq(o.seller, _owner(0));
        assertEq(o.pricePerUnit, 10e6);
        assertEq(o.quantity, 60);
        assertTrue(o.active);
        assertEq(fund.activeOfferOf(req, _projectId(0)), id);
        assertEq(fund.activeOffers(req).length, 1);
    }

    function test_RevertWhen_OfferOnUnknownRequest() public {
        vm.prank(_owner(0));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.UnknownRequest.selector, 7));
        fund.submitOffer(7, _projectId(0), 1, 1);
    }

    function test_RevertWhen_OfferAfterWindow() public {
        uint256 req = _createRequest(100, MAX, 0);
        vm.warp(windowEnd);
        vm.prank(_owner(0));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.OfferWindowClosed.selector, req));
        fund.submitOffer(req, _projectId(0), 1, 1);
    }

    function test_RevertWhen_OfferInvalidAmounts() public {
        uint256 req = _createRequest(100, MAX, 0);
        vm.startPrank(_owner(0));
        vm.expectRevert(AquaForwardFund.InvalidInput.selector);
        fund.submitOffer(req, _projectId(0), 0, 1);
        vm.expectRevert(AquaForwardFund.InvalidInput.selector);
        fund.submitOffer(req, _projectId(0), 1, 0);
        vm.expectRevert(AquaForwardFund.InvalidInput.selector);
        fund.submitOffer(req, _projectId(0), 1, 101);
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.PriceAboveMax.selector, MAX + 1, MAX));
        fund.submitOffer(req, _projectId(0), MAX + 1, 1);
        vm.stopPrank();
    }

    function test_RevertWhen_OfferFromUnregisteredProject() public {
        uint256 req = _createRequest(100, MAX, 0);
        bytes32 ghost = keccak256("not-registered");
        vm.prank(_owner(0));
        vm.expectRevert(abi.encodeWithSelector(AquaRegistry.UnknownProject.selector, ghost));
        fund.submitOffer(req, ghost, 1, 1);
    }

    function test_RevertWhen_OfferFromNonOwner() public {
        uint256 req = _createRequest(100, MAX, 0);
        vm.prank(_owner(1));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.NotProjectOwner.selector, _projectId(0)));
        fund.submitOffer(req, _projectId(0), 1, 1);
    }

    function test_RevertWhen_DuplicateActiveOffer() public {
        uint256 req = _createRequest(100, MAX, 0);
        uint256 id = _offer(req, 0, 10e6, 10);
        vm.prank(_owner(0));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.DuplicateOffer.selector, id));
        fund.submitOffer(req, _projectId(0), 9e6, 10);
    }

    function test_OfferCapIsEnforced() public {
        uint256 req = _createRequest(1000, MAX, 0);
        uint256 cap = fund.MAX_ACTIVE_OFFERS();
        for (uint256 i = 0; i < cap; ++i) {
            if (i >= 4) _registerProject(i);
            _offer(req, i, MAX - i, 10);
        }
        _registerProject(cap);
        vm.prank(_owner(cap));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.TooManyOffers.selector, req));
        fund.submitOffer(req, _projectId(cap), 1, 1);

        // Worst-case award (100 offers, sorted from reverse order) stays far below the block gas limit.
        vm.warp(windowEnd);
        uint256 gasBefore = gasleft();
        fund.award(req);
        uint256 used = gasBefore - gasleft();
        assertLt(used, 15_000_000, "award gas");
        uint256[] memory ids = fund.awardIds(req);
        assertEq(ids.length, cap);
        // Cheapest first: offer 100 has the lowest price (MAX - 99).
        assertEq(ids[0], cap);
        _assertSolvent();
    }

    function test_WithdrawOfferAndResubmit() public {
        uint256 req = _createRequest(100, MAX, 0);
        uint256 a = _offer(req, 0, 10e6, 10);
        uint256 b = _offer(req, 1, 11e6, 10);
        uint256 c = _offer(req, 2, 12e6, 10);
        vm.expectEmit(address(fund));
        emit AquaForwardFund.OfferWithdrawn(a, req);
        vm.prank(_owner(0));
        fund.withdrawOffer(a);
        uint256[] memory active = fund.activeOffers(req);
        assertEq(active.length, 2);
        assertEq(active[0], c); // swapped into the freed slot
        assertEq(active[1], b);
        assertFalse(fund.getOffer(a).active);
        assertEq(fund.activeOfferOf(req, _projectId(0)), 0);
        // Withdraw the last element too (no swap needed).
        vm.prank(_owner(1));
        fund.withdrawOffer(b);
        assertEq(fund.activeOffers(req).length, 1);
        // A withdrawn project may offer again.
        uint256 d = _offer(req, 0, 9e6, 5);
        assertEq(fund.activeOfferOf(req, _projectId(0)), d);
    }

    function test_RevertWhen_WithdrawOfferInvalid() public {
        uint256 req = _createRequest(100, MAX, 0);
        uint256 a = _offer(req, 0, 10e6, 10);
        vm.prank(_owner(1));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.NotOfferSeller.selector, a));
        fund.withdrawOffer(a);
        vm.prank(_owner(0));
        fund.withdrawOffer(a);
        vm.prank(_owner(0));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.OfferInactive.selector, a));
        fund.withdrawOffer(a);
        uint256 b = _offer(req, 1, 10e6, 10);
        vm.warp(windowEnd);
        vm.prank(_owner(1));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.OfferWindowClosed.selector, req));
        fund.withdrawOffer(b);
    }

    // ------------------------------------------------------------------ award

    /// 100 units at max 15, upfront 20 %:
    ///   p0 60 @ 10, p1 30 @ 12 (earlier), p2 50 @ 12 (later, partially filled 10), p3 40 @ 14 (loses).
    function _scenario() internal returns (uint256 req, uint256[4] memory ids) {
        req = _createRequest(100, MAX, 2000);
        ids[0] = _offer(req, 0, 10e6, 60);
        ids[1] = _offer(req, 1, 12e6, 30);
        ids[3] = _offer(req, 3, 14e6, 40);
        ids[2] = _offer(req, 2, 12e6, 50); // ties with p1 but later
        vm.warp(windowEnd);
        fund.award(req);
    }

    function test_AwardSortsByPriceThenTimeWithPartialFill() public {
        uint256 req = _createRequest(100, MAX, 2000);
        uint256 o0 = _offer(req, 0, 10e6, 60);
        uint256 o1 = _offer(req, 1, 12e6, 30);
        uint256 o3 = _offer(req, 3, 14e6, 40);
        uint256 o2 = _offer(req, 2, 12e6, 50);
        vm.warp(windowEnd);

        vm.expectEmit(address(fund));
        emit AquaForwardFund.OfferAwarded(o0, req, _projectId(0), _owner(0), 60, 10e6, 120e6);
        vm.expectEmit(address(fund));
        emit AquaForwardFund.OfferAwarded(o1, req, _projectId(1), _owner(1), 30, 12e6, 72e6);
        vm.expectEmit(address(fund));
        emit AquaForwardFund.OfferAwarded(o2, req, _projectId(2), _owner(2), 10, 12e6, 24e6);
        vm.expectEmit(address(fund));
        emit AquaForwardFund.RequestAwarded(req, 100, 1080e6, 420e6);
        vm.prank(makeAddr("anyone"));
        fund.award(req);

        uint256[] memory ids = fund.awardIds(req);
        assertEq(ids.length, 3);
        assertEq(ids[0], o0);
        assertEq(ids[1], o1);
        assertEq(ids[2], o2);
        assertEq(fund.getAward(o3).quantity, 0, "loser has no award");
        assertEq(fund.getAward(o2).quantity, 10, "partial fill");
        assertEq(fund.getAward(o0).upfrontPaid, 120e6);

        AquaForwardFund.Request memory r = fund.getRequest(req);
        assertTrue(r.awarded);
        assertEq(r.awardedQuantity, 100);
        assertEq(r.escrow, 1080e6 - 216e6);
        assertEq(fund.claimable(buyer), 420e6, "unused escrow refundable now");
        assertEq(fund.claimable(_owner(0)), 120e6);
        assertEq(fund.claimable(_owner(1)), 72e6);
        assertEq(fund.claimable(_owner(2)), 24e6);
        assertEq(fund.claimable(_owner(3)), 0);
        _assertSolvent();

        // Upfront and unused escrow are withdrawable immediately.
        vm.prank(_owner(0));
        fund.withdraw();
        assertEq(usdc.balanceOf(_owner(0)), 120e6);
        vm.prank(buyer);
        fund.withdraw();
        assertEq(usdc.balanceOf(buyer), 420e6);
        _assertSolvent();
    }

    function test_TiesGoToTheEarliestOffer() public {
        uint256 req = _createRequest(10, MAX, 0);
        uint256 a = _offer(req, 0, 11e6, 10);
        uint256 b = _offer(req, 1, 11e6, 10);
        uint256 c = _offer(req, 2, 11e6, 10);
        // Withdrawing `a` swaps `c` into position 0: ties must use offer ids, not positions.
        vm.prank(_owner(0));
        fund.withdrawOffer(a);
        assertEq(fund.activeOffers(req)[0], c);
        vm.warp(windowEnd);
        fund.award(req);
        uint256[] memory ids = fund.awardIds(req);
        assertEq(ids.length, 1);
        assertEq(ids[0], b);
        assertEq(fund.getAward(c).quantity, 0);
    }

    function test_AwardWithNoOffersRefundsEverything() public {
        uint256 req = _createRequest(100, MAX, 3000);
        vm.warp(windowEnd);
        fund.award(req);
        assertEq(fund.claimable(buyer), 100 * MAX);
        assertEq(fund.getRequest(req).escrow, 0);
        assertEq(fund.getRequest(req).awardedQuantity, 0);
        _assertSolvent();
    }

    function test_AwardUnderSubscribed() public {
        uint256 req = _createRequest(100, MAX, 1000);
        _offer(req, 0, 10e6, 30);
        _offer(req, 1, 15e6, 20);
        vm.warp(windowEnd);
        fund.award(req);
        AquaForwardFund.Request memory r = fund.getRequest(req);
        assertEq(r.awardedQuantity, 50);
        assertEq(fund.claimable(buyer), 1500e6 - 600e6);
        assertEq(r.escrow, 600e6 - 60e6);
        _assertSolvent();
    }

    function test_RevertWhen_AwardInvalid() public {
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.UnknownRequest.selector, 1));
        fund.award(1);
        uint256 req = _createRequest(100, MAX, 0);
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.OfferWindowOpen.selector, req));
        fund.award(req);
        vm.warp(windowEnd);
        fund.award(req);
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.AlreadyAwarded.selector, req));
        fund.award(req);
    }

    // ------------------------------------------------------------------ delivery

    function _approveFund(uint256 i) internal {
        vm.prank(_owner(i));
        credit.setApprovalForAll(address(fund), true);
    }

    function test_DeliverInTranchesReleasesExactEscrow() public {
        (uint256 req, uint256[4] memory ids) = _scenario();
        uint256 tokenId = _issueLongFor(_projectId(0), _owner(0), 100);
        _approveFund(0);

        // 25 of 60 units: (600 - 120) * 25 / 60 = 200.
        vm.expectEmit(address(fund));
        emit AquaForwardFund.Delivered(ids[0], tokenId, buyer, 25, 200e6);
        vm.prank(_owner(0));
        fund.deliver(ids[0], tokenId, 25);
        assertEq(credit.balanceOf(buyer, tokenId), 25);
        assertEq(credit.balanceOf(address(fund), tokenId), 0, "never custodies credits");
        assertEq(fund.claimable(_owner(0)), 120e6 + 200e6);

        vm.prank(_owner(0));
        fund.deliver(ids[0], tokenId, 35);
        assertEq(credit.balanceOf(buyer, tokenId), 60);
        assertEq(fund.claimable(_owner(0)), 600e6, "full award = upfront + rest");
        AquaForwardFund.Award memory a = fund.getAward(ids[0]);
        assertEq(a.delivered, 60);
        assertEq(a.released, 480e6);
        assertEq(fund.getRequest(req).escrow, 864e6 - 480e6);
        _assertSolvent();
    }

    function test_DeliverRoundingLeavesNoDust() public {
        // 3 units at 1 base unit each with 10 % upfront: upfront rounds to 0, releases 1,1,1.
        uint256 req = _createRequest(3, 1, 1000);
        uint256 id = _offer(req, 0, 1, 3);
        vm.warp(windowEnd);
        fund.award(req);
        uint256 tokenId = _issueLongFor(_projectId(0), _owner(0), 3);
        _approveFund(0);
        vm.startPrank(_owner(0));
        fund.deliver(id, tokenId, 1);
        fund.deliver(id, tokenId, 1);
        fund.deliver(id, tokenId, 1);
        vm.stopPrank();
        assertEq(fund.claimable(_owner(0)), 3);
        assertEq(fund.getRequest(req).escrow, 0);
        _assertSolvent();
    }

    function test_RevertWhen_DeliverByNonWinner() public {
        (, uint256[4] memory ids) = _scenario();
        uint256 tokenId = _issueLongFor(_projectId(0), _owner(0), 10);
        vm.prank(_owner(1));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.NotAwardSeller.selector, ids[0]));
        fund.deliver(ids[0], tokenId, 1);
        // The losing offer has no award.
        uint256 t3 = _issueLongFor(_projectId(3), _owner(3), 10);
        vm.prank(_owner(3));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.NotAwardSeller.selector, ids[3]));
        fund.deliver(ids[3], t3, 1);
    }

    function test_RevertWhen_DeliverAfterDeadline() public {
        (, uint256[4] memory ids) = _scenario();
        uint256 tokenId = _issueLongFor(_projectId(0), _owner(0), 10);
        _approveFund(0);
        vm.warp(deadline);
        vm.prank(_owner(0));
        fund.deliver(ids[0], tokenId, 1); // the deadline itself is still in time
        vm.warp(uint256(deadline) + 1);
        vm.prank(_owner(0));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.DeadlinePassed.selector, ids[0]));
        fund.deliver(ids[0], tokenId, 1);
    }

    function test_RevertWhen_DeliverZeroOrTooMuch() public {
        (, uint256[4] memory ids) = _scenario();
        uint256 tokenId = _issueLongFor(_projectId(2), _owner(2), 100);
        _approveFund(2);
        vm.startPrank(_owner(2));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.ExceedsAward.selector, 10, 0));
        fund.deliver(ids[2], tokenId, 0);
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.ExceedsAward.selector, 10, 11));
        fund.deliver(ids[2], tokenId, 11);
        vm.stopPrank();
    }

    function test_RevertWhen_DoubleDelivery() public {
        (, uint256[4] memory ids) = _scenario();
        uint256 tokenId = _issueLongFor(_projectId(1), _owner(1), 100);
        _approveFund(1);
        vm.prank(_owner(1));
        fund.deliver(ids[1], tokenId, 30);
        vm.prank(_owner(1));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.ExceedsAward.selector, 0, 30));
        fund.deliver(ids[1], tokenId, 30);
        assertEq(credit.balanceOf(buyer, tokenId), 30);
    }

    function test_RevertWhen_DeliverTemporaryCredits() public {
        (, uint256[4] memory ids) = _scenario();
        uint256 tokenId = _issueFor(_projectId(0), _owner(0), 100, AquaCredit.CreditType.Temporary, FAR_FUTURE);
        _approveFund(0);
        vm.prank(_owner(0));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.NotLongTerm.selector, tokenId));
        fund.deliver(ids[0], tokenId, 1);
    }

    function test_RevertWhen_DeliverUnknownToken() public {
        (, uint256[4] memory ids) = _scenario();
        vm.prank(_owner(0));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.NotLongTerm.selector, 999));
        fund.deliver(ids[0], 999, 1);
    }

    function test_RevertWhen_DeliverOtherProjectsCredits() public {
        (, uint256[4] memory ids) = _scenario();
        // Owner 0 somehow holds project 3's credits: still not deliverable against project 0's award.
        uint256 tokenId = _issueLongFor(_projectId(3), _owner(0), 100);
        _approveFund(0);
        vm.prank(_owner(0));
        vm.expectRevert(
            abi.encodeWithSelector(AquaForwardFund.WrongProject.selector, tokenId, _projectId(0), _projectId(3))
        );
        fund.deliver(ids[0], tokenId, 1);
    }

    function test_RevertWhen_DeliverExpiredCredits() public {
        (, uint256[4] memory ids) = _scenario();
        uint64 expiry = uint64(block.timestamp + 10 days);
        uint256 tokenId = _issueFor(_projectId(0), _owner(0), 100, AquaCredit.CreditType.LongTerm, expiry);
        _approveFund(0);
        vm.warp(expiry);
        vm.prank(_owner(0));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.CreditExpired.selector, tokenId));
        fund.deliver(ids[0], tokenId, 1);
    }

    function test_RevertWhen_DeliverWithoutApproval() public {
        (, uint256[4] memory ids) = _scenario();
        uint256 tokenId = _issueLongFor(_projectId(0), _owner(0), 100);
        vm.prank(_owner(0));
        vm.expectRevert(
            abi.encodeWithSelector(IERC1155Errors.ERC1155MissingApprovalForAll.selector, address(fund), _owner(0))
        );
        fund.deliver(ids[0], tokenId, 1);
    }

    function test_DirectCreditTransfersToFundAreRejected() public {
        uint256 tokenId = _issueLongFor(_projectId(0), _owner(0), 10);
        vm.prank(_owner(0));
        vm.expectRevert(abi.encodeWithSelector(IERC1155Errors.ERC1155InvalidReceiver.selector, address(fund)));
        credit.safeTransferFrom(_owner(0), address(fund), tokenId, 1, "");
    }

    // ------------------------------------------------------------------ refunds

    function test_RefundUndeliveredAfterDeadline() public {
        (uint256 req, uint256[4] memory ids) = _scenario();
        uint256 tokenId = _issueLongFor(_projectId(0), _owner(0), 100);
        _approveFund(0);
        vm.prank(_owner(0));
        fund.deliver(ids[0], tokenId, 25); // releases 200

        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.DeadlineNotPassed.selector, ids[0]));
        fund.refundUndelivered(ids[0]);

        vm.warp(uint256(deadline) + 1);
        // p0: 600 - 120 upfront - 200 released = 280 back; 35 undelivered.
        vm.expectEmit(address(fund));
        emit AquaForwardFund.UndeliveredRefunded(ids[0], req, 35, 280e6);
        fund.refundUndelivered(ids[0]);
        // p1: 360 - 72 = 288; p2: 120 - 24 = 96.
        fund.refundUndelivered(ids[1]);
        fund.refundUndelivered(ids[2]);
        assertEq(fund.claimable(buyer), 420e6 + 280e6 + 288e6 + 96e6);
        assertEq(fund.getRequest(req).escrow, 0);
        assertEq(fund.totalEscrow(), 0);
        // Conservation: what the buyer gets back + what projects earned == the original escrow.
        uint256 projects = fund.claimable(_owner(0)) + fund.claimable(_owner(1)) + fund.claimable(_owner(2));
        assertEq(fund.claimable(buyer) + projects, 1500e6);
        assertEq(projects, 120e6 + 200e6 + 72e6 + 24e6, "upfront is not recovered");
        _assertSolvent();

        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.AlreadySettled.selector, ids[0]));
        fund.refundUndelivered(ids[0]);
    }

    function test_RefundOfFullyDeliveredAwardIsZero() public {
        (, uint256[4] memory ids) = _scenario();
        uint256 tokenId = _issueLongFor(_projectId(2), _owner(2), 10);
        _approveFund(2);
        vm.prank(_owner(2));
        fund.deliver(ids[2], tokenId, 10);
        vm.warp(uint256(deadline) + 1);
        uint256 before = fund.claimable(buyer);
        fund.refundUndelivered(ids[2]);
        assertEq(fund.claimable(buyer), before);
        assertTrue(fund.getAward(ids[2]).settled);
    }

    function test_RevertWhen_RefundUnknownAward() public {
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.NotAwardSeller.selector, 42));
        fund.refundUndelivered(42);
    }

    function test_RevertWhen_WithdrawNothing() public {
        vm.prank(buyer);
        vm.expectRevert(AquaForwardFund.NothingToWithdraw.selector);
        fund.withdraw();
    }

    function test_WithdrawEmitsAndZeroes() public {
        uint256 req = _createRequest(10, MAX, 0);
        vm.warp(windowEnd);
        fund.award(req);
        vm.expectEmit(address(fund));
        emit AquaForwardFund.Withdrawn(buyer, 10 * MAX);
        vm.prank(buyer);
        fund.withdraw();
        assertEq(fund.claimable(buyer), 0);
        assertEq(fund.totalClaimable(), 0);
        assertEq(usdc.balanceOf(address(fund)), 0);
    }

    // ------------------------------------------------------------------ pause semantics

    function test_PauseBlocksNewRiskOnly() public {
        // Set up a live award with upfront before pausing.
        (uint256 req, uint256[4] memory ids) = _scenario();
        uint256 tokenId = _issueLongFor(_projectId(0), _owner(0), 100);
        _approveFund(0);
        windowEnd = uint64(block.timestamp + 30 days); // a second, still-open request
        uint256 req2 = _createRequest(10, MAX, 0);
        uint256 off2 = _offer(req2, 1, 10e6, 5);

        vm.prank(admin);
        fund.pause();

        // Blocked: new requests, offers, deliveries.
        _fundBuyer(buyer, 10 * MAX);
        vm.prank(buyer);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        fund.createRequest(10, MAX, windowEnd, deadline, 0);
        vm.prank(_owner(0));
        vm.expectRevert(Pausable.EnforcedPause.selector);
        fund.submitOffer(req2, _projectId(0), 1, 1);
        vm.prank(_owner(0));
        vm.expectRevert(Pausable.EnforcedPause.selector);
        fund.deliver(ids[0], tokenId, 1);

        // Allowed: withdrawing an offer, awarding (frees unused escrow), refunds and withdrawals.
        vm.prank(_owner(1));
        fund.withdrawOffer(off2);
        vm.warp(windowEnd);
        fund.award(req2);
        vm.prank(buyer);
        fund.withdraw();
        vm.prank(_owner(0));
        fund.withdraw(); // upfront share already earned
        vm.warp(uint256(deadline) + 1);
        fund.refundUndelivered(ids[0]);
        vm.prank(buyer);
        fund.withdraw();
        assertEq(fund.getRequest(req).escrow, 864e6 - 480e6);
        _assertSolvent();

        vm.prank(admin);
        fund.unpause();
        vm.prank(_owner(0));
        vm.expectRevert(abi.encodeWithSelector(AquaForwardFund.DeadlinePassed.selector, ids[0]));
        fund.deliver(ids[0], tokenId, 1);
    }

    // ------------------------------------------------------------------ reentrancy

    function test_BuyerHookCannotReenter() public {
        ReentrantBuyer evil = new ReentrantBuyer(fund);
        _fundBuyer(address(evil), 10 * MAX);
        vm.prank(address(evil));
        uint256 req = fund.createRequest(10, MAX, windowEnd, deadline, 0);
        uint256 id = _offer(req, 0, MAX, 10);
        vm.warp(windowEnd);
        fund.award(req);
        uint256 tokenId = _issueLongFor(_projectId(0), _owner(0), 10);
        _approveFund(0);
        evil.arm(id, tokenId);
        vm.prank(_owner(0));
        vm.expectRevert(); // ReentrancyGuardReentrantCall bubbles up through the hook
        fund.deliver(id, tokenId, 1);
    }
}

/// A buyer contract that tries to re-enter `withdraw` from its ERC-1155 receive hook.
contract ReentrantBuyer {
    AquaForwardFund internal immutable fund;
    bool internal armed;

    constructor(AquaForwardFund fund_) {
        fund = fund_;
    }

    function arm(uint256, uint256) external {
        armed = true;
    }

    function onERC1155Received(address, address, uint256, uint256, bytes calldata) external returns (bytes4) {
        if (armed) fund.withdraw();
        return this.onERC1155Received.selector;
    }
}
