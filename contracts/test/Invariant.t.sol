// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Test} from "forge-std/Test.sol";
import {AquaCredit} from "../src/AquaCredit.sol";
import {AquaRegistry} from "../src/AquaRegistry.sol";
import {Base} from "./Base.t.sol";

/// Random sequences of issue / transfer / retire / new reports / reversals / replacements.
contract Handler is Test {
    AquaRegistry internal registry;
    AquaCredit internal credit;
    address internal registrar;
    address internal verifier;
    address internal issuer;
    bytes32 internal project;
    address[3] internal holders;
    uint32 internal year = 2026;
    uint256[] public reports;

    constructor(AquaRegistry r, AquaCredit c, address reg, address ver, address iss, bytes32 p) {
        registry = r;
        credit = c;
        registrar = reg;
        verifier = ver;
        issuer = iss;
        project = p;
        holders = [makeAddr("h0"), makeAddr("h1"), makeAddr("h2")];
    }

    function reportCount() external view returns (uint256) {
        return reports.length;
    }

    function newReport(int256 lccc) external {
        lccc = bound(lccc, -2_000, 10_000);
        year += 1;
        vm.prank(registrar);
        uint256 id = registry.submitReport(project, year, keccak256(abi.encode(year)), lccc, "");
        vm.prank(verifier);
        registry.approveReport(id);
        reports.push(id);
    }

    function issue(uint256 reportSeed, bool longTerm, uint256 holderSeed, uint256 amount) external {
        if (reports.length == 0) return;
        uint256 id = reports[reportSeed % reports.length];
        amount = bound(amount, 1, 12_000);
        vm.prank(issuer);
        try credit.issue(
            id,
            longTerm ? AquaCredit.CreditType.LongTerm : AquaCredit.CreditType.Temporary,
            holders[holderSeed % 3],
            amount,
            4_102_444_800
        ) {}
            catch {}
    }

    function transfer(uint256 reportSeed, bool longTerm, uint256 fromSeed, uint256 toSeed, uint256 amount) external {
        if (reports.length == 0) return;
        uint256 tokenId = credit.tokenIdFor(
            reports[reportSeed % reports.length],
            longTerm ? AquaCredit.CreditType.LongTerm : AquaCredit.CreditType.Temporary
        );
        address from = holders[fromSeed % 3];
        amount = bound(amount, 0, credit.balanceOf(from, tokenId));
        vm.prank(from);
        credit.safeTransferFrom(from, holders[toSeed % 3], tokenId, amount, "");
    }

    function retire(uint256 reportSeed, bool longTerm, uint256 holderSeed, uint256 amount) external {
        if (reports.length == 0) return;
        uint256 tokenId = credit.tokenIdFor(
            reports[reportSeed % reports.length],
            longTerm ? AquaCredit.CreditType.LongTerm : AquaCredit.CreditType.Temporary
        );
        address holder = holders[holderSeed % 3];
        amount = bound(amount, 0, credit.balanceOf(holder, tokenId));
        if (amount == 0) return;
        vm.prank(holder);
        credit.retire(tokenId, amount, "beneficiary", "purpose");
    }

    function replace(uint256 reportSeed, uint256 holderSeed, uint256 amount) external {
        if (reports.length == 0) return;
        uint256 tokenId = credit.tokenIdFor(reports[reportSeed % reports.length], AquaCredit.CreditType.LongTerm);
        address holder = holders[holderSeed % 3];
        uint256 owed = registry.replacementOwed(project);
        uint256 maxAmount = credit.balanceOf(holder, tokenId);
        if (owed < maxAmount) maxAmount = owed;
        amount = bound(amount, 0, maxAmount);
        if (amount == 0) return;
        vm.prank(holder);
        credit.replaceReversal(tokenId, amount);
    }
}

contract CreditInvariants is Base {
    Handler internal handler;

    function setUp() public override {
        super.setUp();
        _register();
        handler = new Handler(registry, credit, registrar, verifier, issuer, PROJECT);
        targetContract(address(handler));
    }

    /// Credits in circulation + retired + burned for replacement never exceed what was approved.
    function invariant_IssuanceNeverExceedsApproval() public view {
        uint256 n = handler.reportCount();
        for (uint256 i = 0; i < n; ++i) {
            uint256 reportId = handler.reports(i);
            AquaRegistry.Report memory r = registry.getReport(reportId);
            uint256 longId = credit.tokenIdFor(reportId, AquaCredit.CreditType.LongTerm);
            uint256 tempId = credit.tokenIdFor(reportId, AquaCredit.CreditType.Temporary);
            // casting is safe: only evaluated when lccc > 0
            // forge-lint: disable-next-line(unsafe-typecast)
            uint256 lcap = r.lccc > 0 ? uint256(r.lccc) : 0;
            assertLe(credit.totalIssued(longId), lcap, "lCCC over-issued");
            assertLe(credit.totalIssued(tempId), r.tccc, "tCCC over-issued");
            assertLe(credit.totalSupply(longId) + credit.totalRetired(longId), credit.totalIssued(longId));
            assertLe(credit.totalSupply(tempId) + credit.totalRetired(tempId), credit.totalIssued(tempId));
        }
    }

    /// Whenever a replacement obligation is open, no report can be issued against.
    function invariant_NoIssuanceWhileReplacementOwed() public {
        if (registry.replacementOwed(PROJECT) == 0 || handler.reportCount() == 0) return;
        uint256 reportId = handler.reports(0);
        vm.prank(issuer);
        vm.expectRevert();
        credit.issue(reportId, AquaCredit.CreditType.Temporary, ngo, 1, FAR_FUTURE);
    }
}
