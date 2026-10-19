"""Domain enumerations. Stored as VARCHAR + CHECK constraint (not native PG enums) so adding a value
is a simple migration."""

from enum import StrEnum


class PlatformRole(StrEnum):
    """Platform-wide role. Organisation-scoped roles live on Membership."""

    USER = "user"
    VERIFIER = "verifier"
    REGISTRY_ADMIN = "registry_admin"


class OrgType(StrEnum):
    NGO = "ngo"
    PANCHAYAT = "panchayat"
    COMPANY = "company"  # buyers
    GOVERNMENT = "government"


class OrgRole(StrEnum):
    ADMIN = "admin"
    FIELD_AGENT = "field_agent"
    MEMBER = "member"


class Ecosystem(StrEnum):
    MANGROVE = "mangrove"
    SALT_MARSH = "salt_marsh"
    SEAGRASS = "seagrass"


class Methodology(StrEnum):
    CCTS_BM_FR05_001 = "ccts_bm_fr05_001"
    VM0033_V2_1 = "vm0033_v2_1"


class ProjectStatus(StrEnum):
    """Lifecycle (see docs/IMPLEMENTATION_PLAN.md §3). Transitions are enforced in services."""

    DRAFT = "draft"
    SUBMITTED = "submitted"
    BASELINE_READY = "baseline_ready"
    CHANGES_REQUESTED = "changes_requested"
    REGISTERED = "registered"
    MONITORING = "monitoring"
    REJECTED = "rejected"


class TenureType(StrEnum):
    """Who controls the land (intertidal land has no ordinary registry; see RESEARCH_NOTES Q1)."""

    FOREST_LAND = "forest_land"
    REVENUE_GOVERNMENT_LAND = "revenue_government_land"
    PORT_OR_CENTRAL_LAND = "port_or_central_land"
    COMMUNITY_PANCHAYAT = "community_panchayat"
    PRIVATE_LAND = "private_land"


class DocumentKind(StrEnum):
    FOREST_DEPT_AGREEMENT = "forest_dept_agreement"
    COLLECTOR_NOC = "collector_noc"
    PORT_OR_CENTRAL_PERMISSION = "port_or_central_permission"
    PANCHAYAT_RESOLUTION = "panchayat_resolution"
    FPIC_RECORD = "fpic_record"
    LAND_TITLE = "land_title"
    OWNER_CONSENT = "owner_consent"
    CRZ_CLEARANCE = "crz_clearance"
    CARBON_RIGHTS_AGREEMENT = "carbon_rights_agreement"
    OTHER = "other"


# Evidence required before a project can be submitted, per tenure type.
REQUIRED_TENURE_DOCUMENTS: dict[TenureType, frozenset[DocumentKind]] = {
    TenureType.FOREST_LAND: frozenset({DocumentKind.FOREST_DEPT_AGREEMENT}),
    TenureType.REVENUE_GOVERNMENT_LAND: frozenset({DocumentKind.COLLECTOR_NOC}),
    TenureType.PORT_OR_CENTRAL_LAND: frozenset({DocumentKind.PORT_OR_CENTRAL_PERMISSION}),
    TenureType.COMMUNITY_PANCHAYAT: frozenset(
        {DocumentKind.PANCHAYAT_RESOLUTION, DocumentKind.FPIC_RECORD}
    ),
    TenureType.PRIVATE_LAND: frozenset({DocumentKind.LAND_TITLE, DocumentKind.OWNER_CONSENT}),
}
# Required for every tenure type.
ALWAYS_REQUIRED_DOCUMENTS: frozenset[DocumentKind] = frozenset(
    {DocumentKind.CRZ_CLEARANCE, DocumentKind.CARBON_RIGHTS_AGREEMENT}
)


class PeriodKind(StrEnum):
    BASELINE = "baseline"
    MONITORING = "monitoring"


class PeriodStatus(StrEnum):
    OPEN = "open"  # collecting field evidence
    ANALYSING = "analysing"
    UNDER_REVIEW = "under_review"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    REJECTED = "rejected"


class SubmissionKind(StrEnum):
    PLOT_CENTRE = "plot_centre"  # photo at a sample-plot marker
    PHOTO_POINT = "photo_point"  # fixed photo-monitoring point
    GENERAL = "general"


class SubmissionStatus(StrEnum):
    PENDING_CHECKS = "pending_checks"
    CHECKED = "checked"  # all automatic checks ran; verifier decides
    REJECTED = "rejected"  # a hard check failed (outside boundary, duplicate, bad nonce)


class AnalysisKind(StrEnum):
    BACKFILL = "backfill"  # all scenes since the backfill start date (once, at registration)
    INCREMENTAL = "incremental"  # nightly: only scenes newer than the latest observation
    COMPOSITE = "composite"  # seasonal (dry-season) median + percentile composite


class AnalysisStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AlertKind(StrEnum):
    VEGETATION_LOSS = "vegetation_loss"


class AlertSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
