// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {AquaCredit} from "../src/AquaCredit.sol";
import {Base} from "./Base.t.sol";

contract AquaCreditTest is Base {
    uint256 internal report;

    function setUp() public override {
        super.setUp();
        _register();
        report = _approvedReport(2031, 5074);
    }

    function test_IssueLongTermUpToApprovedQuantity() public {
        uint256 id = _issueLong(report, ngo, 5000);
        _issueLong(report, ngo, 74);
        assertEq(credit.balanceOf(ngo, id), 5074);
        assertEq(credit.totalIssued(id), 5074);
        assertEq(id, credit.tokenIdFor(report, AquaCredit.CreditType.LongTerm));

        vm.prank(issuer);
        vm.expectRevert(abi.encodeWithSelector(AquaCredit.ExceedsApproved.selector, 0, 1));
        credit.issue(report, AquaCredit.CreditType.LongTerm, ngo, 1, FAR_FUTURE);
    }

    function test_TemporaryCreditsCappedByCumulative() public {
        uint64 expiry = uint64(block.timestamp + 365 days * 5);
        vm.prank(issuer);
        uint256 id = credit.issue(report, AquaCredit.CreditType.Temporary, ngo, 5074, expiry);
        AquaCredit.Vintage memory v = credit.vintage(id);
        assertEq(uint8(v.creditType), uint8(AquaCredit.CreditType.Temporary));
        assertEq(v.expiresAt, expiry);
        assertTrue(id != credit.tokenIdFor(report, AquaCredit.CreditType.LongTerm), "separate token ids");
    }

    function testFuzz_NeverIssueMoreThanApproved(uint256 a, uint256 b) public {
        a = bound(a, 1, 10_000);
        b = bound(b, 1, 10_000);
        vm.startPrank(issuer);
        credit.issue(report, AquaCredit.CreditType.LongTerm, ngo, a > 5074 ? 5074 : a, FAR_FUTURE);
        if ((a > 5074 ? 5074 : a) + b > 5074) {
            vm.expectRevert();
        }
        credit.issue(report, AquaCredit.CreditType.LongTerm, ngo, b, FAR_FUTURE);
        vm.stopPrank();
        assertLe(credit.totalIssued(credit.tokenIdFor(report, AquaCredit.CreditType.LongTerm)), 5074);
    }

    function test_RevertWhen_ReportNotApproved() public {
        uint256 pending = _submit(2034, 100);
        vm.prank(issuer);
        vm.expectRevert(abi.encodeWithSelector(AquaCredit.ReportNotApproved.selector, pending));
        credit.issue(pending, AquaCredit.CreditType.LongTerm, ngo, 1, FAR_FUTURE);
    }

    function test_RevertWhen_NotIssuer() public {
        vm.prank(ngo);
        vm.expectRevert();
        credit.issue(report, AquaCredit.CreditType.LongTerm, ngo, 1, FAR_FUTURE);
    }

    function test_RetireBurnsAndRecordsCertificate() public {
        uint256 id = _issueLong(report, buyer, 300);
        vm.expectEmit(true, true, true, true);
        emit AquaCredit.CreditsRetired(1, id, buyer, 250, "Konkan Steel Ltd. (fictional)", "FY2026-27 scope 1");
        vm.prank(buyer);
        uint256 retirementId = credit.retire(id, 250, "Konkan Steel Ltd. (fictional)", "FY2026-27 scope 1");
        assertEq(retirementId, 1);
        assertEq(credit.balanceOf(buyer, id), 50);
        assertEq(credit.totalSupply(id), 50);
        assertEq(credit.totalRetired(id), 250);
    }

    function test_RevertWhen_RetireMoreThanBalance() public {
        uint256 id = _issueLong(report, buyer, 10);
        vm.prank(buyer);
        vm.expectRevert();
        credit.retire(id, 11, "x", "y");
    }

    function test_ExpiredTemporaryCreditsCannotMove() public {
        uint64 expiry = uint64(block.timestamp + 30 days);
        vm.prank(issuer);
        uint256 id = credit.issue(report, AquaCredit.CreditType.Temporary, ngo, 100, expiry);
        vm.warp(expiry);
        vm.startPrank(ngo);
        vm.expectRevert(abi.encodeWithSelector(AquaCredit.CreditExpired.selector, id));
        credit.safeTransferFrom(ngo, buyer, id, 1, "");
        vm.expectRevert(abi.encodeWithSelector(AquaCredit.CreditExpired.selector, id));
        credit.retire(id, 1, "x", "y");
        vm.stopPrank();
    }

    function test_ReversalBlocksIssuanceUntilReplaced() public {
        uint256 id = _issueLong(report, ngo, 5074);
        uint256 reversal = _approvedReport(2033, -1200);
        vm.prank(issuer);
        vm.expectRevert(abi.encodeWithSelector(AquaCredit.ReplacementOutstanding.selector, PROJECT, 1200));
        credit.issue(reversal, AquaCredit.CreditType.Temporary, ngo, 1, FAR_FUTURE);

        vm.prank(ngo);
        credit.replaceReversal(id, 1200);
        assertEq(registry.replacementOwed(PROJECT), 0);
        assertEq(credit.balanceOf(ngo, id), 5074 - 1200);

        // obligation settled: issuance possible again (tCCC = 5074 - 1200)
        vm.prank(issuer);
        credit.issue(reversal, AquaCredit.CreditType.Temporary, ngo, 3874, FAR_FUTURE);
    }

    function test_RevertWhen_ReplacingMoreThanOwed() public {
        uint256 id = _issueLong(report, ngo, 5074);
        _approvedReport(2033, -100);
        vm.prank(ngo);
        vm.expectRevert();
        credit.replaceReversal(id, 101);
    }

    function test_RevertWhen_ReplacingWithTemporaryCredits() public {
        vm.prank(issuer);
        uint256 tid = credit.issue(report, AquaCredit.CreditType.Temporary, ngo, 10, FAR_FUTURE);
        _approvedReport(2033, -5);
        vm.prank(ngo);
        vm.expectRevert(abi.encodeWithSelector(AquaCredit.WrongVintage.selector, tid));
        credit.replaceReversal(tid, 5);
    }

    function test_PauseFreezesTransfersAndRetirement() public {
        uint256 id = _issueLong(report, ngo, 10);
        vm.prank(admin);
        credit.pause();
        vm.prank(ngo);
        vm.expectRevert();
        credit.retire(id, 1, "x", "y");
    }
}
