// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Ownable, Ownable2Step} from "@openzeppelin/contracts/access/Ownable2Step.sol";
import {IERC1155} from "@openzeppelin/contracts/token/ERC1155/IERC1155.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @title AquaMarketplace
/// @notice Fixed-price listings of AquaCredit (ERC-1155) for a stable ERC-20 (mock USDC on testnet).
/// @dev Non-custodial: sellers keep their credits until a sale (they grant operator approval).
///      Payments use the pull pattern: proceeds accrue and are withdrawn separately, so a failing
///      recipient can never block a sale. Fee is capped at 10%.
contract AquaMarketplace is Ownable2Step, ReentrancyGuard {
    using SafeERC20 for IERC20;

    struct Listing {
        address seller;
        uint256 tokenId;
        uint256 remaining;
        uint256 pricePerUnit;
        bool active;
    }

    uint16 public constant MAX_FEE_BPS = 1000;

    IERC1155 public immutable credit;
    IERC20 public immutable paymentToken;
    address public treasury;
    uint16 public feeBps;
    uint256 public listingCount;
    mapping(uint256 listingId => Listing) public listings;
    mapping(address account => uint256) public proceeds;

    event Listed(
        uint256 indexed listingId, address indexed seller, uint256 indexed tokenId, uint256 amount, uint256 pricePerUnit
    );
    event Purchased(uint256 indexed listingId, address indexed buyer, uint256 amount, uint256 totalPrice, uint256 fee);
    event Cancelled(uint256 indexed listingId);
    event Withdrawn(address indexed account, uint256 amount);
    event FeeChanged(uint16 feeBps, address treasury);

    error InvalidInput();
    error NotSeller();
    error ListingInactive();
    error InsufficientListing(uint256 remaining, uint256 requested);
    error NotApproved();
    error PriceAboveLimit(uint256 total, uint256 maxTotal);
    error NothingToWithdraw();

    constructor(address owner_, IERC1155 credit_, IERC20 paymentToken_, address treasury_, uint16 feeBps_)
        Ownable(owner_)
    {
        if (treasury_ == address(0) || feeBps_ > MAX_FEE_BPS) revert InvalidInput();
        credit = credit_;
        paymentToken = paymentToken_;
        treasury = treasury_;
        feeBps = feeBps_;
    }

    function list(uint256 tokenId, uint256 amount, uint256 pricePerUnit) external returns (uint256 listingId) {
        if (amount == 0 || pricePerUnit == 0) revert InvalidInput();
        if (!credit.isApprovedForAll(msg.sender, address(this))) revert NotApproved();
        if (credit.balanceOf(msg.sender, tokenId) < amount) {
            revert InsufficientListing(credit.balanceOf(msg.sender, tokenId), amount);
        }
        listingId = ++listingCount;
        listings[listingId] = Listing(msg.sender, tokenId, amount, pricePerUnit, true);
        emit Listed(listingId, msg.sender, tokenId, amount, pricePerUnit);
    }

    /// @param maxTotalPrice slippage guard: revert if the total exceeds what the buyer agreed to.
    function buy(uint256 listingId, uint256 amount, uint256 maxTotalPrice) external nonReentrant {
        Listing storage l = listings[listingId];
        if (!l.active) revert ListingInactive();
        if (amount == 0 || amount > l.remaining) revert InsufficientListing(l.remaining, amount);
        uint256 total = amount * l.pricePerUnit;
        if (total > maxTotalPrice) revert PriceAboveLimit(total, maxTotalPrice);
        uint256 fee = (total * feeBps) / 10_000;

        // Effects before interactions.
        l.remaining -= amount;
        if (l.remaining == 0) l.active = false;
        proceeds[l.seller] += total - fee;
        proceeds[treasury] += fee;

        paymentToken.safeTransferFrom(msg.sender, address(this), total);
        credit.safeTransferFrom(l.seller, msg.sender, l.tokenId, amount, "");
        emit Purchased(listingId, msg.sender, amount, total, fee);
    }

    function cancel(uint256 listingId) external {
        Listing storage l = listings[listingId];
        if (l.seller != msg.sender) revert NotSeller();
        if (!l.active) revert ListingInactive();
        l.active = false;
        emit Cancelled(listingId);
    }

    function withdraw() external nonReentrant {
        uint256 amount = proceeds[msg.sender];
        if (amount == 0) revert NothingToWithdraw();
        proceeds[msg.sender] = 0;
        paymentToken.safeTransfer(msg.sender, amount);
        emit Withdrawn(msg.sender, amount);
    }

    function setFee(uint16 feeBps_, address treasury_) external onlyOwner {
        if (treasury_ == address(0) || feeBps_ > MAX_FEE_BPS) revert InvalidInput();
        feeBps = feeBps_;
        treasury = treasury_;
        emit FeeChanged(feeBps_, treasury_);
    }
}
