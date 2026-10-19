// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @title MockUSDC
/// @notice TESTNET-ONLY stablecoin stand-in (6 decimals) used to price credits on Polygon Amoy.
///         Has no value. Anyone may claim a small daily faucet amount for demos.
contract MockUSDC is ERC20, Ownable {
    uint256 public constant FAUCET_AMOUNT = 1_000e6;
    uint256 public constant FAUCET_COOLDOWN = 1 days;
    mapping(address account => uint256) public lastFaucet;

    error FaucetCooldown(uint256 availableAt);

    constructor(address owner_) ERC20("Mock USD Coin (testnet)", "mUSDC") Ownable(owner_) {}

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }

    function faucet() external {
        uint256 next = lastFaucet[msg.sender] + FAUCET_COOLDOWN;
        // daily faucet on a testnet token: timestamp drift is irrelevant
        // forge-lint: disable-next-line(block-timestamp)
        if (lastFaucet[msg.sender] != 0 && block.timestamp < next) revert FaucetCooldown(next);
        lastFaucet[msg.sender] = block.timestamp;
        _mint(msg.sender, FAUCET_AMOUNT);
    }
}
