// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {AquaCredit} from "../src/AquaCredit.sol";
import {AquaForwardFund} from "../src/AquaForwardFund.sol";
import {Base} from "./Base.t.sol";

/// Base deployment + an AquaForwardFund, with helpers to register projects and issue their credits.
abstract contract FundBase is Base {
    AquaForwardFund internal fund;

    uint256 internal constant PRICE = 12e6; // 12 mUSDC per tCO2e
    uint64 internal windowEnd;
    uint64 internal deadline;

    mapping(bytes32 projectId => uint32) internal lastYear;

    function setUp() public virtual override {
        super.setUp();
        fund = new AquaForwardFund(admin, registry, credit, usdc);
        windowEnd = uint64(block.timestamp + 30 days);
        deadline = uint64(block.timestamp + 5 * 365 days);
    }

    function _projectId(uint256 i) internal pure returns (bytes32) {
        return keccak256(abi.encode("project", i));
    }

    function _owner(uint256 i) internal returns (address) {
        return makeAddr(string(abi.encodePacked("owner", vm.toString(i))));
    }

    /// Registers project i (owner = _owner(i)), returns its id.
    function _registerProject(uint256 i) internal returns (bytes32 id) {
        id = _projectId(i);
        vm.prank(registrar);
        registry.registerProject(id, _owner(i), BOUNDARY, 2026, "");
        lastYear[id] = 2026;
    }

    /// Approves a new report for `projectId` and issues `amount` credits of `creditType` to `to`.
    function _issueFor(bytes32 projectId, address to, uint256 amount, AquaCredit.CreditType creditType, uint64 expiry)
        internal
        returns (uint256 tokenId)
    {
        uint32 year = ++lastYear[projectId];
        // casting is safe: test amounts are far below int256 max
        // forge-lint: disable-next-line(unsafe-typecast)
        int256 lccc = int256(amount);
        vm.prank(registrar);
        uint256 reportId =
            registry.submitReport(projectId, year, keccak256(abi.encode(projectId, year)), lccc, "ipfs://report");
        vm.prank(verifier);
        registry.approveReport(reportId);
        vm.prank(issuer);
        tokenId = credit.issue(reportId, creditType, to, amount, expiry);
    }

    function _issueLongFor(bytes32 projectId, address to, uint256 amount) internal returns (uint256) {
        return _issueFor(projectId, to, amount, AquaCredit.CreditType.LongTerm, FAR_FUTURE);
    }

    function _fundBuyer(address who, uint256 amount) internal {
        vm.prank(admin);
        usdc.mint(who, amount);
        vm.prank(who);
        usdc.approve(address(fund), type(uint256).max);
    }

    function _createRequest(uint256 quantity, uint256 maxPrice, uint16 upfrontBps) internal returns (uint256) {
        _fundBuyer(buyer, quantity * maxPrice);
        vm.prank(buyer);
        return fund.createRequest(quantity, maxPrice, windowEnd, deadline, upfrontBps);
    }

    function _offer(uint256 requestId, uint256 i, uint256 price, uint256 quantity) internal returns (uint256) {
        vm.prank(_owner(i));
        return fund.submitOffer(requestId, _projectId(i), price, quantity);
    }

    /// Escrow + claimable must always be exactly what the contract holds.
    function _assertSolvent() internal view {
        assertEq(usdc.balanceOf(address(fund)), fund.totalEscrow() + fund.totalClaimable(), "insolvent");
    }
}
