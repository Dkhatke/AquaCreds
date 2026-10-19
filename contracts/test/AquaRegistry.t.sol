// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {AquaRegistry} from "../src/AquaRegistry.sol";
import {Base} from "./Base.t.sol";

contract AquaRegistryTest is Base {
    function test_RegisterProject() public {
        _register();
        AquaRegistry.Project memory p = registry.getProject(PROJECT);
        assertEq(p.owner, ngo);
        assertEq(p.boundaryHash, BOUNDARY);
        assertEq(p.startYear, 2026);
        assertEq(p.lastVerifiedYear, 2026);
    }

    function test_RevertWhen_RegisterTwice() public {
        _register();
        vm.expectRevert(abi.encodeWithSelector(AquaRegistry.ProjectExists.selector, PROJECT));
        _register();
    }

    function test_RevertWhen_StartBefore2025() public {
        vm.prank(registrar);
        vm.expectRevert(AquaRegistry.InvalidInput.selector);
        registry.registerProject(PROJECT, ngo, BOUNDARY, 2024, "");
    }

    function test_RevertWhen_NotRegistrar() public {
        bytes32 role = registry.REGISTRAR_ROLE(); // read before prank: prank applies to the next call only
        vm.prank(ngo);
        vm.expectRevert(abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, ngo, role));
        registry.registerProject(PROJECT, ngo, BOUNDARY, 2026, "");
    }

    function test_ReportLifecycleAndContiguousPeriods() public {
        _register();
        uint256 r1 = _approvedReport(2031, 5074);
        AquaRegistry.Report memory rep = registry.getReport(r1);
        assertEq(uint8(rep.status), uint8(AquaRegistry.ReportStatus.Approved));
        assertEq(rep.periodStart, 2026);
        assertEq(rep.periodEnd, 2031);
        assertEq(rep.tccc, 5074);
        assertEq(rep.verifier, verifier);

        uint256 r2 = _approvedReport(2034, 3000);
        rep = registry.getReport(r2);
        assertEq(rep.periodStart, 2031, "next period starts where the last ended");
        assertEq(rep.tccc, 8074, "tCCC is cumulative");
    }

    function test_RevertWhen_PeriodDoesNotAdvance() public {
        _register();
        _approvedReport(2031, 100);
        vm.prank(registrar);
        vm.expectRevert(AquaRegistry.InvalidInput.selector);
        registry.submitReport(PROJECT, 2031, keccak256("x"), 1, "");
    }

    function test_RevertWhen_SecondReportWhilePending() public {
        _register();
        uint256 r1 = _submit(2031, 100);
        vm.prank(registrar);
        vm.expectRevert(abi.encodeWithSelector(AquaRegistry.ReportPending.selector, r1));
        registry.submitReport(PROJECT, 2032, keccak256("y"), 1, "");
    }

    function test_RevertWhen_SubmitterApprovesOwnReport() public {
        _register();
        bytes32 role = registry.VERIFIER_ROLE();
        vm.prank(admin);
        registry.grantRole(role, registrar);
        uint256 r1 = _submit(2031, 100);
        vm.prank(registrar);
        vm.expectRevert(AquaRegistry.SelfApproval.selector);
        registry.approveReport(r1);
    }

    function test_RejectFreesProjectForResubmission() public {
        _register();
        uint256 r1 = _submit(2031, 100);
        vm.prank(verifier);
        registry.rejectReport(r1, keccak256("insufficient plots"));
        assertEq(uint8(registry.getReport(r1).status), uint8(AquaRegistry.ReportStatus.Rejected));
        // project period did not advance; resubmission for the same period is allowed
        uint256 r2 = _submit(2031, 90);
        assertEq(registry.getReport(r2).periodStart, 2026);
    }

    function test_RevertWhen_ApproveTwice() public {
        _register();
        uint256 r1 = _approvedReport(2031, 100);
        vm.prank(verifier);
        vm.expectRevert(
            abi.encodeWithSelector(AquaRegistry.WrongReportStatus.selector, r1, AquaRegistry.ReportStatus.Approved)
        );
        registry.approveReport(r1);
    }

    function test_ReversalCreatesReplacementObligation() public {
        _register();
        _approvedReport(2031, 5000);
        uint256 r2 = _approvedReport(2033, -1200); // cyclone damage
        assertEq(registry.replacementOwed(PROJECT), 1200);
        assertEq(registry.getReport(r2).tccc, 3800, "cumulative falls but stays positive");
    }

    function test_RevertWhen_QuantityOutOfBounds() public {
        _register();
        vm.startPrank(registrar);
        vm.expectRevert(AquaRegistry.InvalidInput.selector);
        registry.submitReport(PROJECT, 2031, keccak256("z"), type(int256).min, "");
        vm.expectRevert(AquaRegistry.InvalidInput.selector);
        registry.submitReport(PROJECT, 2031, keccak256("z"), 1e15 + 1, "");
        vm.stopPrank();
    }

    function test_RevertWhen_SettleWithoutCreditRole() public {
        bytes32 role = registry.CREDIT_CONTRACT_ROLE();
        vm.prank(ngo);
        vm.expectRevert(abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, ngo, role));
        registry.settleReplacement(PROJECT, 1);
    }

    function test_PauseBlocksWrites() public {
        vm.prank(admin);
        registry.pause();
        vm.prank(registrar);
        vm.expectRevert();
        registry.registerProject(PROJECT, ngo, BOUNDARY, 2026, "");
    }
}
