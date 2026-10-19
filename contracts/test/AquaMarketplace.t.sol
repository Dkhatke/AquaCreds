// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {AquaMarketplace} from "../src/AquaMarketplace.sol";
import {Base} from "./Base.t.sol";

contract AquaMarketplaceTest is Base {
    uint256 internal tokenId;
    uint256 internal constant PRICE = 18e6; // 18 mUSDC per tCO2e

    function setUp() public override {
        super.setUp();
        _register();
        tokenId = _issueLong(_approvedReport(2031, 5074), ngo, 5074);
        vm.prank(ngo);
        credit.setApprovalForAll(address(market), true);
        vm.prank(admin);
        usdc.mint(buyer, 100_000e6);
        vm.prank(buyer);
        usdc.approve(address(market), type(uint256).max);
    }

    function _list(uint256 amount) internal returns (uint256 id) {
        vm.prank(ngo);
        id = market.list(tokenId, amount, PRICE);
    }

    function test_BuyTransfersCreditsAndAccruesProceeds() public {
        uint256 id = _list(1000);
        vm.prank(buyer);
        market.buy(id, 250, 250 * PRICE);

        assertEq(credit.balanceOf(buyer, tokenId), 250);
        assertEq(credit.balanceOf(ngo, tokenId), 5074 - 250);
        uint256 total = 250 * PRICE;
        uint256 fee = (total * 200) / 10_000;
        assertEq(market.proceeds(ngo), total - fee);
        assertEq(market.proceeds(treasury), fee);
        (,, uint256 remaining,, bool active) = market.listings(id);
        assertEq(remaining, 750);
        assertTrue(active);

        vm.prank(ngo);
        market.withdraw();
        assertEq(usdc.balanceOf(ngo), total - fee);
        assertEq(market.proceeds(ngo), 0);
    }

    function test_ListingClosesWhenSoldOut() public {
        uint256 id = _list(10);
        vm.prank(buyer);
        market.buy(id, 10, 10 * PRICE);
        (,,,, bool active) = market.listings(id);
        assertFalse(active);
        vm.prank(buyer);
        vm.expectRevert(AquaMarketplace.ListingInactive.selector);
        market.buy(id, 1, PRICE);
    }

    function test_RevertWhen_PriceAboveBuyerLimit() public {
        uint256 id = _list(10);
        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(AquaMarketplace.PriceAboveLimit.selector, 10 * PRICE, 10 * PRICE - 1));
        market.buy(id, 10, 10 * PRICE - 1);
    }

    function test_RevertWhen_ListingWithoutApproval() public {
        vm.prank(ngo);
        credit.setApprovalForAll(address(market), false);
        vm.prank(ngo);
        vm.expectRevert(AquaMarketplace.NotApproved.selector);
        market.list(tokenId, 1, PRICE);
    }

    function test_RevertWhen_ListingMoreThanBalance() public {
        vm.prank(ngo);
        vm.expectRevert(abi.encodeWithSelector(AquaMarketplace.InsufficientListing.selector, 5074, 5075));
        market.list(tokenId, 5075, PRICE);
    }

    function test_OnlySellerCancels() public {
        uint256 id = _list(10);
        vm.prank(buyer);
        vm.expectRevert(AquaMarketplace.NotSeller.selector);
        market.cancel(id);
        vm.prank(ngo);
        market.cancel(id);
        (,,,, bool active) = market.listings(id);
        assertFalse(active);
    }

    function test_FeeIsCappedAndOwnerOnly() public {
        vm.prank(admin);
        vm.expectRevert(AquaMarketplace.InvalidInput.selector);
        market.setFee(1001, treasury);
        vm.prank(buyer);
        vm.expectRevert();
        market.setFee(100, treasury);
    }

    function test_BuyerCanRetirePurchasedCredits() public {
        uint256 id = _list(100);
        vm.startPrank(buyer);
        market.buy(id, 100, 100 * PRICE);
        credit.retire(tokenId, 100, "Konkan Steel Ltd. (fictional)", "FY2026-27");
        vm.stopPrank();
        assertEq(credit.totalRetired(tokenId), 100);
    }

    function test_FaucetCooldown() public {
        vm.startPrank(buyer);
        usdc.faucet();
        vm.expectRevert();
        usdc.faucet();
        vm.warp(block.timestamp + 1 days);
        usdc.faucet();
        vm.stopPrank();
        assertEq(usdc.balanceOf(buyer), 100_000e6 + 2_000e6);
    }
}
