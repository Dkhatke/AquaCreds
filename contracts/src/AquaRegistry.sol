// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";

/// @title AquaRegistry
/// @notice On-chain anchor for blue-carbon projects and their verified monitoring reports.
/// @dev Full evidence lives off-chain (private storage + IPFS); this contract stores hashes,
///      approved quantities and the decision trail. Quantities are whole tCO2e units computed by
///      the off-chain carbon engine under India CCTS BM FR05.001:
///        - lccc  = net removals in the period (negative = reversal)
///        - tccc  = cumulative net removals since project start (never below zero)
///      A negative lccc creates a replacement obligation that must be settled (by burning
///      long-term credits of the same project) before any further issuance.
contract AquaRegistry is AccessControl, Pausable {
    bytes32 public constant REGISTRAR_ROLE = keccak256("REGISTRAR_ROLE");
    bytes32 public constant VERIFIER_ROLE = keccak256("VERIFIER_ROLE");
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");
    /// @notice Granted only to the AquaCredit contract (settles replacement obligations).
    bytes32 public constant CREDIT_CONTRACT_ROLE = keccak256("CREDIT_CONTRACT_ROLE");

    enum ReportStatus {
        None,
        Submitted,
        Approved,
        Rejected
    }

    struct Project {
        address owner;
        bytes32 boundaryHash;
        uint32 startYear;
        uint32 lastVerifiedYear;
        uint256 pendingReportId;
        bool exists;
        string metadataURI;
    }

    struct Report {
        bytes32 projectId;
        uint32 periodStart;
        uint32 periodEnd;
        bytes32 reportHash;
        int256 lccc;
        uint256 tccc;
        ReportStatus status;
        address submitter;
        address verifier;
        uint64 decidedAt;
        string evidenceURI;
    }

    mapping(bytes32 projectId => Project) private _projects;
    mapping(uint256 reportId => Report) private _reports;
    mapping(bytes32 projectId => int256) public cumulativeRemovals;
    mapping(bytes32 projectId => uint256) public replacementOwed;
    uint256 public reportCount;
    /// @notice Sanity bound on per-report quantities (also keeps int256 negation/casts safe).
    int256 public constant MAX_ABS_QUANTITY = 1e15;

    event ProjectRegistered(
        bytes32 indexed projectId, address indexed owner, bytes32 boundaryHash, uint32 startYear, string metadataURI
    );
    event ReportSubmitted(
        uint256 indexed reportId,
        bytes32 indexed projectId,
        uint32 periodStart,
        uint32 periodEnd,
        bytes32 reportHash,
        int256 lccc,
        string evidenceURI
    );
    event ReportApproved(
        uint256 indexed reportId, bytes32 indexed projectId, address indexed verifier, int256 lccc, uint256 tccc
    );
    event ReportRejected(
        uint256 indexed reportId, bytes32 indexed projectId, address indexed verifier, bytes32 reasonHash
    );
    event ReplacementOwed(bytes32 indexed projectId, uint256 amount, uint256 totalOwed);
    event ReplacementSettled(bytes32 indexed projectId, uint256 amount, uint256 remainingOwed);

    error ProjectExists(bytes32 projectId);
    error UnknownProject(bytes32 projectId);
    error InvalidInput();
    error PeriodGap(uint32 expectedStart, uint32 givenStart);
    error ReportPending(uint256 reportId);
    error WrongReportStatus(uint256 reportId, ReportStatus status);
    error SelfApproval();
    error SettlementExceedsOwed(uint256 owed, uint256 amount);

    constructor(address admin) {
        if (admin == address(0)) revert InvalidInput();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(PAUSER_ROLE, admin);
    }

    // ------------------------------------------------------------------ projects

    function registerProject(
        bytes32 projectId,
        address owner,
        bytes32 boundaryHash,
        uint32 startYear,
        string calldata metadataURI
    ) external onlyRole(REGISTRAR_ROLE) whenNotPaused {
        if (_projects[projectId].exists) revert ProjectExists(projectId);
        if (projectId == bytes32(0) || owner == address(0) || boundaryHash == bytes32(0) || startYear < 2025) {
            revert InvalidInput();
        }
        _projects[projectId] = Project({
            owner: owner,
            boundaryHash: boundaryHash,
            startYear: startYear,
            lastVerifiedYear: startYear,
            pendingReportId: 0,
            exists: true,
            metadataURI: metadataURI
        });
        emit ProjectRegistered(projectId, owner, boundaryHash, startYear, metadataURI);
    }

    // ------------------------------------------------------------------ reports

    /// @notice Submit a monitoring report for verification. Periods must be contiguous.
    function submitReport(
        bytes32 projectId,
        uint32 periodEnd,
        bytes32 reportHash,
        int256 lccc,
        string calldata evidenceURI
    ) external onlyRole(REGISTRAR_ROLE) whenNotPaused returns (uint256 reportId) {
        Project storage p = _projects[projectId];
        if (!p.exists) revert UnknownProject(projectId);
        if (p.pendingReportId != 0) revert ReportPending(p.pendingReportId);
        if (reportHash == bytes32(0) || periodEnd <= p.lastVerifiedYear) revert InvalidInput();
        if (lccc > MAX_ABS_QUANTITY || lccc < -MAX_ABS_QUANTITY) revert InvalidInput();

        reportId = ++reportCount;
        _reports[reportId] = Report({
            projectId: projectId,
            periodStart: p.lastVerifiedYear,
            periodEnd: periodEnd,
            reportHash: reportHash,
            lccc: lccc,
            tccc: 0,
            status: ReportStatus.Submitted,
            submitter: msg.sender,
            verifier: address(0),
            decidedAt: 0,
            evidenceURI: evidenceURI
        });
        p.pendingReportId = reportId;
        emit ReportSubmitted(reportId, projectId, p.lastVerifiedYear, periodEnd, reportHash, lccc, evidenceURI);
    }

    function approveReport(uint256 reportId) external onlyRole(VERIFIER_ROLE) whenNotPaused {
        Report storage r = _requireSubmitted(reportId);
        if (r.submitter == msg.sender) revert SelfApproval();
        Project storage p = _projects[r.projectId];

        int256 cumulative = cumulativeRemovals[r.projectId] + r.lccc;
        cumulativeRemovals[r.projectId] = cumulative;
        // casting to uint256 is safe because cumulative > 0 is checked first
        // forge-lint: disable-next-line(unsafe-typecast)
        r.tccc = cumulative > 0 ? uint256(cumulative) : 0;
        r.status = ReportStatus.Approved;
        r.verifier = msg.sender;
        r.decidedAt = uint64(block.timestamp);
        p.lastVerifiedYear = r.periodEnd;
        p.pendingReportId = 0;

        if (r.lccc < 0) {
            // safe: lccc is bounded by MAX_ABS_QUANTITY, so -lccc cannot overflow and is > 0 here
            // forge-lint: disable-next-line(unsafe-typecast)
            uint256 owed = uint256(-r.lccc);
            replacementOwed[r.projectId] += owed;
            emit ReplacementOwed(r.projectId, owed, replacementOwed[r.projectId]);
        }
        emit ReportApproved(reportId, r.projectId, msg.sender, r.lccc, r.tccc);
    }

    function rejectReport(uint256 reportId, bytes32 reasonHash) external onlyRole(VERIFIER_ROLE) whenNotPaused {
        Report storage r = _requireSubmitted(reportId);
        if (r.submitter == msg.sender) revert SelfApproval();
        r.status = ReportStatus.Rejected;
        r.verifier = msg.sender;
        r.decidedAt = uint64(block.timestamp);
        _projects[r.projectId].pendingReportId = 0;
        emit ReportRejected(reportId, r.projectId, msg.sender, reasonHash);
    }

    /// @notice Called by AquaCredit when long-term credits are burned to cover a reversal.
    function settleReplacement(bytes32 projectId, uint256 amount) external onlyRole(CREDIT_CONTRACT_ROLE) {
        uint256 owed = replacementOwed[projectId];
        if (amount == 0 || amount > owed) revert SettlementExceedsOwed(owed, amount);
        replacementOwed[projectId] = owed - amount;
        emit ReplacementSettled(projectId, amount, owed - amount);
    }

    // ------------------------------------------------------------------ admin

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(PAUSER_ROLE) {
        _unpause();
    }

    // ------------------------------------------------------------------ views

    function getProject(bytes32 projectId) external view returns (Project memory) {
        if (!_projects[projectId].exists) revert UnknownProject(projectId);
        return _projects[projectId];
    }

    function getReport(uint256 reportId) external view returns (Report memory) {
        return _reports[reportId];
    }

    function _requireSubmitted(uint256 reportId) private view returns (Report storage r) {
        r = _reports[reportId];
        if (r.status != ReportStatus.Submitted) revert WrongReportStatus(reportId, r.status);
    }
}
