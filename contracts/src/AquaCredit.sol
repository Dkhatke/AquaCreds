// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {ERC1155} from "@openzeppelin/contracts/token/ERC1155/ERC1155.sol";
import {ERC1155Supply} from "@openzeppelin/contracts/token/ERC1155/extensions/ERC1155Supply.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {AquaRegistry} from "./AquaRegistry.sol";

/// @title AquaCredit
/// @notice Carbon credits as ERC-1155 tokens: one token id per (verified report, credit type).
/// @dev Credit types follow India CCTS BM FR05.001:
///        Temporary (tCCC): cumulative removals since project start; expire at a set time.
///        LongTerm  (lCCC): removals in the report's period; replaced if a later reversal occurs.
///      Issuance is capped by the approved quantities in AquaRegistry, so the issuer can never
///      mint more than a verifier approved. Retirement burns tokens and emits a certificate event.
///      TESTNET REGISTRY UNITS: not legally recognised carbon credits.
contract AquaCredit is ERC1155Supply, AccessControl, Pausable {
    bytes32 public constant ISSUER_ROLE = keccak256("ISSUER_ROLE");
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");

    enum CreditType {
        Temporary,
        LongTerm
    }

    struct Vintage {
        bytes32 projectId;
        uint256 reportId;
        CreditType creditType;
        uint64 expiresAt;
        bool exists;
    }

    AquaRegistry public immutable registry;
    mapping(uint256 tokenId => Vintage) private _vintages;
    mapping(uint256 tokenId => uint256) public totalIssued;
    mapping(uint256 tokenId => uint256) public totalRetired;
    uint256 public retirementCount;

    event CreditsIssued(
        uint256 indexed tokenId,
        uint256 indexed reportId,
        bytes32 indexed projectId,
        CreditType creditType,
        address to,
        uint256 amount,
        uint64 expiresAt
    );
    event CreditsRetired(
        uint256 indexed retirementId,
        uint256 indexed tokenId,
        address indexed holder,
        uint256 amount,
        string beneficiary,
        string purpose
    );
    event ReversalReplaced(bytes32 indexed projectId, uint256 indexed tokenId, address indexed holder, uint256 amount);

    error ReportNotApproved(uint256 reportId);
    error ReplacementOutstanding(bytes32 projectId, uint256 owed);
    error ExceedsApproved(uint256 cap, uint256 requested);
    error InvalidExpiry();
    error CreditExpired(uint256 tokenId);
    error WrongVintage(uint256 tokenId);
    error ZeroAmount();

    constructor(address admin, AquaRegistry registry_, string memory uri_) ERC1155(uri_) {
        registry = registry_;
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(PAUSER_ROLE, admin);
    }

    function tokenIdFor(uint256 reportId, CreditType creditType) public pure returns (uint256) {
        return (reportId << 1) | uint256(creditType);
    }

    function vintage(uint256 tokenId) external view returns (Vintage memory) {
        return _vintages[tokenId];
    }

    /// @notice Mint credits for an approved report, capped by the verified quantity.
    function issue(uint256 reportId, CreditType creditType, address to, uint256 amount, uint64 expiresAt)
        external
        onlyRole(ISSUER_ROLE)
        whenNotPaused
        returns (uint256 tokenId)
    {
        if (amount == 0) revert ZeroAmount();
        AquaRegistry.Report memory r = registry.getReport(reportId);
        if (r.status != AquaRegistry.ReportStatus.Approved) revert ReportNotApproved(reportId);
        uint256 owed = registry.replacementOwed(r.projectId);
        if (owed != 0) revert ReplacementOutstanding(r.projectId, owed);

        // casting to uint256 is safe because r.lccc > 0 is checked first
        // forge-lint: disable-next-line(unsafe-typecast)
        uint256 cap = creditType == CreditType.LongTerm ? (r.lccc > 0 ? uint256(r.lccc) : 0) : r.tccc;
        tokenId = tokenIdFor(reportId, creditType);
        uint256 already = totalIssued[tokenId];
        if (already + amount > cap) revert ExceedsApproved(cap - already, amount);

        Vintage storage v = _vintages[tokenId];
        if (!v.exists) {
            // expiry is measured in years; seconds of validator drift are irrelevant
            // forge-lint: disable-next-line(block-timestamp)
            if (expiresAt <= block.timestamp) revert InvalidExpiry();
            _vintages[tokenId] = Vintage(r.projectId, reportId, creditType, expiresAt, true);
        }
        totalIssued[tokenId] = already + amount;
        _mint(to, tokenId, amount, "");
        emit CreditsIssued(tokenId, reportId, r.projectId, creditType, to, amount, _vintages[tokenId].expiresAt);
    }

    /// @notice Permanently retire credits (claim the offset). Emits the certificate record.
    function retire(uint256 tokenId, uint256 amount, string calldata beneficiary, string calldata purpose)
        external
        returns (uint256 retirementId)
    {
        if (amount == 0) revert ZeroAmount();
        _burn(msg.sender, tokenId, amount); // expiry + pause checked in _update
        totalRetired[tokenId] += amount;
        retirementId = ++retirementCount;
        emit CreditsRetired(retirementId, tokenId, msg.sender, amount, beneficiary, purpose);
    }

    /// @notice Burn long-term credits of the same project to settle a reversal obligation.
    function replaceReversal(uint256 tokenId, uint256 amount) external {
        Vintage memory v = _vintages[tokenId];
        if (!v.exists || v.creditType != CreditType.LongTerm) revert WrongVintage(tokenId);
        _burn(msg.sender, tokenId, amount);
        registry.settleReplacement(v.projectId, amount);
        emit ReversalReplaced(v.projectId, tokenId, msg.sender, amount);
    }

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(PAUSER_ROLE) {
        _unpause();
    }

    /// @dev Blocks every movement (transfer, retirement, replacement) of expired credits and all
    ///      movements while paused. Minting happens before expiry by construction.
    function _update(address from, address to, uint256[] memory ids, uint256[] memory values)
        internal
        override
        whenNotPaused
    {
        if (from != address(0)) {
            for (uint256 i = 0; i < ids.length; ++i) {
                uint64 expiresAt = _vintages[ids[i]].expiresAt;
                // forge-lint: disable-next-line(block-timestamp)
                if (expiresAt != 0 && block.timestamp >= expiresAt) revert CreditExpired(ids[i]);
            }
        }
        super._update(from, to, ids, values);
    }

    function supportsInterface(bytes4 interfaceId) public view override(ERC1155, AccessControl) returns (bool) {
        return super.supportsInterface(interfaceId);
    }
}
