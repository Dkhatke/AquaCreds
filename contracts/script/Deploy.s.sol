// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Script, console} from "forge-std/Script.sol";
import {AquaCredit} from "../src/AquaCredit.sol";
import {AquaForwardFund} from "../src/AquaForwardFund.sol";
import {AquaMarketplace} from "../src/AquaMarketplace.sol";
import {AquaRegistry} from "../src/AquaRegistry.sol";
import {MockUSDC} from "../src/MockUSDC.sol";

/// Deploys the AquaCreds contract suite and wires roles.
///
/// Simulate (no network, no keys):   ./forge.sh script script/Deploy.s.sol
/// Polygon Amoy:  ./forge.sh script script/Deploy.s.sol --rpc-url $AMOY_RPC_URL --broadcast \
///                  --private-key $DEPLOYER_KEY --verify
///
/// Role holders come from env (default: the deployer, for local testing):
///   ADMIN_ADDRESS     DEFAULT_ADMIN_ROLE + PAUSER_ROLE on registry, credit and forward fund
///                     (use a multisig/hardware wallet in prod)
///   REGISTRAR_ADDRESS platform relayer that registers projects and submits reports
///   VERIFIER_ADDRESS  accredited verifier (MUST differ from the registrar)
///   ISSUER_ADDRESS    registry admin that issues credits after approval
///   TREASURY_ADDRESS  marketplace fee recipient
///   FEE_BPS           marketplace fee in basis points (default 200 = 2%)
contract Deploy is Script {
    function run()
        external
        returns (AquaRegistry registry, AquaCredit credit, AquaMarketplace market, MockUSDC usdc, AquaForwardFund fund)
    {
        address deployer = msg.sender;
        address admin = vm.envOr("ADMIN_ADDRESS", deployer);
        address registrar = vm.envOr("REGISTRAR_ADDRESS", deployer);
        address verifier = vm.envOr("VERIFIER_ADDRESS", address(0xBEEF));
        address issuer = vm.envOr("ISSUER_ADDRESS", deployer);
        address treasury = vm.envOr("TREASURY_ADDRESS", deployer);
        uint16 feeBps = uint16(vm.envOr("FEE_BPS", uint256(200)));
        string memory uri = vm.envOr("CREDIT_URI", string("https://registry.aquacreds.example/api/credits/{id}.json"));
        require(verifier != registrar, "verifier must differ from registrar");

        vm.startBroadcast();
        // Deployer is temporary admin to wire roles, then hands over.
        registry = new AquaRegistry(deployer);
        credit = new AquaCredit(deployer, registry, uri);
        usdc = new MockUSDC(admin);
        market = new AquaMarketplace(admin, credit, usdc, treasury, feeBps);
        // Reverse-auction forward funding: reads projects from the registry and vintages from the
        // credit contract; needs no roles on either. The admin gets its admin + pauser roles directly.
        fund = new AquaForwardFund(admin, registry, credit, usdc);

        registry.grantRole(registry.REGISTRAR_ROLE(), registrar);
        registry.grantRole(registry.VERIFIER_ROLE(), verifier);
        registry.grantRole(registry.CREDIT_CONTRACT_ROLE(), address(credit));
        credit.grantRole(credit.ISSUER_ROLE(), issuer);
        if (admin != deployer) {
            registry.grantRole(registry.DEFAULT_ADMIN_ROLE(), admin);
            registry.grantRole(registry.PAUSER_ROLE(), admin);
            credit.grantRole(credit.DEFAULT_ADMIN_ROLE(), admin);
            credit.grantRole(credit.PAUSER_ROLE(), admin);
            registry.renounceRole(registry.PAUSER_ROLE(), deployer);
            registry.renounceRole(registry.DEFAULT_ADMIN_ROLE(), deployer);
            credit.renounceRole(credit.PAUSER_ROLE(), deployer);
            credit.renounceRole(credit.DEFAULT_ADMIN_ROLE(), deployer);
        }
        vm.stopBroadcast();

        console.log("AquaRegistry   ", address(registry));
        console.log("AquaCredit     ", address(credit));
        console.log("AquaMarketplace", address(market));
        console.log("MockUSDC       ", address(usdc));
        console.log("AquaForwardFund", address(fund));
    }
}
