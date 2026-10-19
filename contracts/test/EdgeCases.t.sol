// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IERC1155} from "@openzeppelin/contracts/token/ERC1155/IERC1155.sol";
import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {AquaCredit} from "../src/AquaCredit.sol";
import {AquaMarketplace} from "../src/AquaMarketplace.sol";
import {AquaRegistry} from "../src/AquaRegistry.sol";
import {Base} from "./Base.t.sol";

/// Error paths and admin functions not covered by the scenario tests.
contract EdgeCasesTest is Base {
    function test_RegistryConstructorRejectsZeroAdmin() public {
        vm.expectRevert(AquaRegistry.InvalidInput.selector);
        new AquaRegistry(address(0));
    }

    function test_GetUnknownProjectReverts() public {
        vm.expectRevert(abi.encodeWithSelector(AquaRegistry.UnknownProject.selector, PROJECT));
        registry.getProject(PROJECT);
    }

    function test_SubmitForUnknownProjectReverts() public {
        vm.prank(registrar);
        vm.expectRevert(abi.encodeWithSelector(AquaRegistry.UnknownProject.selector, PROJECT));
        registry.submitReport(PROJECT, 2031, keccak256("r"), 1, "");
    }

    function test_SelfRejectionReverts() public {
        _register();
        bytes32 role = registry.VERIFIER_ROLE();
        vm.prank(admin);
        registry.grantRole(role, registrar);
        uint256 id = _submit(2031, 1);
        vm.prank(registrar);
        vm.expectRevert(AquaRegistry.SelfApproval.selector);
        registry.rejectReport(id, bytes32(0));
    }

    function test_RegistryUnpauseRestoresWrites() public {
        vm.startPrank(admin);
        registry.pause();
        registry.unpause();
        vm.stopPrank();
        _register();
        assertTrue(registry.getProject(PROJECT).exists);
    }

    function test_CreditZeroAmountAndBadExpiry() public {
        _register();
        uint256 report = _approvedReport(2031, 100);
        vm.startPrank(issuer);
        vm.expectRevert(AquaCredit.ZeroAmount.selector);
        credit.issue(report, AquaCredit.CreditType.LongTerm, ngo, 0, FAR_FUTURE);
        vm.expectRevert(AquaCredit.InvalidExpiry.selector);
        credit.issue(report, AquaCredit.CreditType.LongTerm, ngo, 1, uint64(block.timestamp));
        vm.stopPrank();
        vm.prank(ngo);
        vm.expectRevert(AquaCredit.ZeroAmount.selector);
        credit.retire(1, 0, "x", "y");
    }

    function test_CreditUnpauseAndInterfaces() public {
        vm.startPrank(admin);
        credit.pause();
        credit.unpause();
        vm.stopPrank();
        assertTrue(credit.supportsInterface(type(IERC1155).interfaceId));
        assertTrue(credit.supportsInterface(type(IAccessControl).interfaceId));
        assertFalse(credit.supportsInterface(0xdeadbeef));
    }

    function test_MarketplaceConstructorAndFeeValidation() public {
        vm.expectRevert(AquaMarketplace.InvalidInput.selector);
        new AquaMarketplace(admin, credit, usdc, address(0), 100);
        vm.expectRevert(AquaMarketplace.InvalidInput.selector);
        new AquaMarketplace(admin, credit, usdc, treasury, 1001);

        address newTreasury = makeAddr("newTreasury");
        vm.prank(admin);
        market.setFee(50, newTreasury);
        assertEq(market.feeBps(), 50);
        assertEq(market.treasury(), newTreasury);
        vm.prank(admin);
        vm.expectRevert(AquaMarketplace.InvalidInput.selector);
        market.setFee(50, address(0));
    }

    function test_MarketplaceRejectsBadListingsAndWithdrawals() public {
        vm.startPrank(ngo);
        vm.expectRevert(AquaMarketplace.InvalidInput.selector);
        market.list(1, 0, 1);
        vm.expectRevert(AquaMarketplace.InvalidInput.selector);
        market.list(1, 1, 0);
        vm.expectRevert(AquaMarketplace.NothingToWithdraw.selector);
        market.withdraw();
        vm.stopPrank();
    }

    function test_MarketplaceBuyZeroAndCancelInactive() public {
        _register();
        uint256 tokenId = _issueLong(_approvedReport(2031, 100), ngo, 100);
        vm.startPrank(ngo);
        credit.setApprovalForAll(address(market), true);
        uint256 id = market.list(tokenId, 10, 1e6);
        market.cancel(id);
        vm.expectRevert(AquaMarketplace.ListingInactive.selector);
        market.cancel(id);
        vm.stopPrank();

        vm.prank(ngo);
        uint256 id2 = market.list(tokenId, 10, 1e6);
        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(AquaMarketplace.InsufficientListing.selector, 10, 0));
        market.buy(id2, 0, 0);
    }

    function test_MockUsdcBasics() public {
        assertEq(usdc.decimals(), 6);
        vm.prank(admin);
        usdc.mint(buyer, 5e6);
        assertEq(IERC20(address(usdc)).balanceOf(buyer), 5e6);
        vm.prank(buyer);
        vm.expectRevert();
        usdc.mint(buyer, 1);
    }
}
