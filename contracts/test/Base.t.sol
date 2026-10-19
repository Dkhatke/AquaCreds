// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Test} from "forge-std/Test.sol";
import {AquaCredit} from "../src/AquaCredit.sol";
import {AquaMarketplace} from "../src/AquaMarketplace.sol";
import {AquaRegistry} from "../src/AquaRegistry.sol";
import {MockUSDC} from "../src/MockUSDC.sol";

/// Shared deployment: separate addresses for admin, registrar (platform), verifier, issuer.
abstract contract Base is Test {
    address internal admin = makeAddr("admin");
    address internal registrar = makeAddr("registrar");
    address internal verifier = makeAddr("verifier");
    address internal issuer = makeAddr("issuer");
    address internal ngo = makeAddr("ngo");
    address internal buyer = makeAddr("buyer");
    address internal treasury = makeAddr("treasury");

    AquaRegistry internal registry;
    AquaCredit internal credit;
    AquaMarketplace internal market;
    MockUSDC internal usdc;

    bytes32 internal constant PROJECT = keccak256("bhitarkanika-fringe-restoration");
    bytes32 internal constant BOUNDARY = keccak256("boundary-geojson-v1");
    uint64 internal constant FAR_FUTURE = 4_102_444_800; // 2100-01-01

    function setUp() public virtual {
        vm.warp(1_767_225_600); // 2026-01-01
        registry = new AquaRegistry(admin);
        credit = new AquaCredit(admin, registry, "https://registry.aquacreds.example/api/credits/{id}.json");
        usdc = new MockUSDC(admin);
        market = new AquaMarketplace(admin, credit, usdc, treasury, 200); // 2 % fee

        vm.startPrank(admin);
        registry.grantRole(registry.REGISTRAR_ROLE(), registrar);
        registry.grantRole(registry.VERIFIER_ROLE(), verifier);
        registry.grantRole(registry.CREDIT_CONTRACT_ROLE(), address(credit));
        credit.grantRole(credit.ISSUER_ROLE(), issuer);
        vm.stopPrank();
    }

    function _register() internal {
        vm.prank(registrar);
        registry.registerProject(PROJECT, ngo, BOUNDARY, 2026, "ipfs://bafy-project-metadata");
    }

    function _submit(uint32 periodEnd, int256 lccc) internal returns (uint256 reportId) {
        vm.prank(registrar);
        reportId = registry.submitReport(
            PROJECT, periodEnd, keccak256(abi.encode(periodEnd, lccc)), lccc, "ipfs://bafy-report"
        );
    }

    function _approvedReport(uint32 periodEnd, int256 lccc) internal returns (uint256 reportId) {
        reportId = _submit(periodEnd, lccc);
        vm.prank(verifier);
        registry.approveReport(reportId);
    }

    function _issueLong(uint256 reportId, address to, uint256 amount) internal returns (uint256 tokenId) {
        vm.prank(issuer);
        tokenId = credit.issue(reportId, AquaCredit.CreditType.LongTerm, to, amount, FAR_FUTURE);
    }
}
