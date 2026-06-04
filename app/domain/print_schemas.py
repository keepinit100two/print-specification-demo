"""
Print-specification workflow contracts.

These models are PROJECT-SPECIFIC and intentionally live alongside (not inside)
the reusable template contracts in app/domain/schemas.py.

Pipeline shape (intake -> normalization -> spec resolution -> compliance ->
adaptation -> generation -> validation -> approval -> production):

    RawSubmission        (intake)
        -> DesignJob             (normalization)
        -> PrintSpecification    (resolved downstream from DesignJob + config)
        -> ComplianceResult      (submitted-image readiness vs. PrintSpecification)
        -> AdaptationPlan        (deterministic transformation intent)
        -> GenerationRequest     (strict, model-ready)
        -> GeneratedCandidate    (model outputs)
        -> ValidationResult      (gate before approval)
        -> ApprovalDecision      (human decision)
        -> ProductionPackage     (final approved output)

PrintWorkflowRunResult bundles these stage outputs for orchestration
logs / debug output. Nothing here is wired into main.py/router.py/actuator.py yet.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PrintWorkflowStage(str, Enum):
    """
    Coarse stage label for the print workflow.

    A stage is a *phase* of the pipeline (where we are), distinct from the
    fine-grained state-machine position captured by PrintWorkflowState.
    """

    INTAKE = "intake"
    NORMALIZATION = "normalization"
    SPECIFICATION = "specification"
    COMPLIANCE = "compliance"
    ADAPTATION = "adaptation"
    GENERATION = "generation"
    VALIDATION = "validation"
    APPROVAL = "approval"
    PRODUCTION = "production"
    COMPLETED = "completed"
    FAILED = "failed"


class PrintWorkflowState(str, Enum):
    """
    Legal state-machine states for a print workflow run.

    Unlike PrintWorkflowStage (a coarse phase label), these represent the
    concrete, transition-bearing states a run can occupy — including the
    pending/running/complete/failed sub-states within each phase as well as
    terminal outcomes.
    """

    SUBMITTED = "submitted"
    INGESTED = "ingested"

    NORMALIZATION_PENDING = "normalization_pending"
    NORMALIZED = "normalized"
    NORMALIZATION_NEEDS_REVIEW = "normalization_needs_review"
    NORMALIZATION_FAILED = "normalization_failed"

    SPECIFICATION_PENDING = "specification_pending"
    SPECIFICATION_RESOLVED = "specification_resolved"
    SPECIFICATION_FAILED = "specification_failed"

    COMPLIANCE_PENDING = "compliance_pending"
    COMPLIANCE_COMPLETE = "compliance_complete"
    COMPLIANCE_FAILED = "compliance_failed"

    ADAPTATION_PLANNED = "adaptation_planned"

    GENERATION_PENDING = "generation_pending"
    GENERATION_RUNNING = "generation_running"
    GENERATION_COMPLETE = "generation_complete"
    GENERATION_FAILED = "generation_failed"

    VALIDATION_PENDING = "validation_pending"
    VALIDATION_COMPLETE = "validation_complete"
    VALIDATION_FAILED = "validation_failed"

    OWNER_REVIEW_PENDING = "owner_review_pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"

    PRODUCTION_PACKAGING_PENDING = "production_packaging_pending"
    PRODUCTION_PACKAGE_CREATED = "production_package_created"

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResultStatus(str, Enum):
    """Generic outcome status shared by stage result models."""

    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    SKIPPED = "skipped"
    PENDING = "pending"


class FailureSeverity(str, Enum):
    """Severity for an individual issue/finding within a stage result."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AssetRole(str, Enum):
    """Role a submitted/generated asset plays in the print job."""

    PRIMARY = "primary"
    BACKGROUND = "background"
    LOGO = "logo"
    REFERENCE = "reference"
    OVERLAY = "overlay"
    MOCKUP = "mockup"


class ProductType(str, Enum):
    """Physical product the artwork is destined for."""

    BUSINESS_CARD = "business_card"
    FLYER = "flyer"
    POSTER = "poster"
    BROCHURE = "brochure"
    STICKER = "sticker"
    BANNER = "banner"
    APPAREL = "apparel"
    PACKAGING = "packaging"


class TransformationType(str, Enum):
    """Deterministic transformation intents an AdaptationPlan can request."""

    RESIZE = "resize"
    CROP = "crop"
    PAD = "pad"
    UPSCALE = "upscale"
    RECOLOR = "recolor"
    BACKGROUND_REMOVAL = "background_removal"
    DPI_ADJUSTMENT = "dpi_adjustment"
    COLOR_PROFILE_CONVERSION = "color_profile_conversion"
    BLEED_EXTENSION = "bleed_extension"


class ApprovalStatus(str, Enum):
    """Human approval outcome."""

    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    PENDING = "pending"


class InvocationStatus(str, Enum):
    """Outcome of a single model invocation (AI Generation Service)."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------


class ContractProvenance(BaseModel):
    """
    Shared lineage/provenance for a contract instance.

    Lets any stage output record where it came from (source + inputs it was
    derived from), which stage produced it, and under which config version —
    so a full run can be audited and replayed deterministically.
    """

    source_id: Optional[str] = Field(
        None,
        description="Id of the primary source this contract was produced from",
    )
    source_type: Optional[str] = Field(
        None,
        description="Type of the source, e.g. raw_submission, design_job, generation_request",
    )
    derived_from_ids: List[str] = Field(
        default_factory=list,
        description="Ids of all upstream contracts this one was derived from",
    )
    created_by_stage: Optional[PrintWorkflowStage] = Field(
        None,
        description="Stage that produced this contract",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When this contract instance was created",
    )
    config_version: Optional[str] = Field(
        None,
        description="Config/policy version in effect when this contract was created",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional provenance/debug context",
    )


class StageIssue(BaseModel):
    """A single finding raised by a stage (reason/warning detail carrier)."""

    code: str = Field(..., description="Machine-readable issue code")
    message: str = Field(..., description="Human-readable description of the issue")
    severity: FailureSeverity = Field(
        FailureSeverity.WARNING,
        description="How serious this finding is",
    )
    field: Optional[str] = Field(
        None,
        description="Field/attribute this issue refers to, if applicable",
    )


class ImageProperties(BaseModel):
    """Observed properties of a concrete image asset."""

    width_px: Optional[int] = Field(None, description="Pixel width")
    height_px: Optional[int] = Field(None, description="Pixel height")
    dpi: Optional[int] = Field(None, description="Dots per inch, if known")
    color_profile: Optional[str] = Field(
        None,
        description="Color profile/space, e.g. sRGB, CMYK, Adobe RGB",
    )
    file_format: Optional[str] = Field(
        None,
        description="File format, e.g. png, jpeg, tiff, pdf",
    )
    has_transparency: Optional[bool] = Field(
        None,
        description="Whether the asset has an alpha channel",
    )


class SubmittedAsset(BaseModel):
    """An asset attached to the raw submission."""

    asset_id: str = Field(..., description="Unique id for this asset")
    role: AssetRole = Field(AssetRole.PRIMARY, description="Role in the print job")
    uri: str = Field(..., description="Location/reference of the asset (path or URL)")
    properties: ImageProperties = Field(
        default_factory=ImageProperties,
        description="Observed image properties, if extracted",
    )


# ---------------------------------------------------------------------------
# Stage 1: Intake
# ---------------------------------------------------------------------------


class RawSubmission(BaseModel):
    """
    INTAKE. Raw, untrusted input as received from the requester.

    This is deliberately loose: it captures what the user gave us before any
    normalization or validation has happened.
    """

    submission_id: str = Field(..., description="Unique idempotency anchor for the submission")
    submitted_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the submission was received",
    )
    requester: Optional[str] = Field(
        None,
        description="Who submitted (user id/email), if available",
    )
    requested_product: Optional[str] = Field(
        None,
        description="Free-text product the user asked for (not yet a ProductType)",
    )
    brief: Optional[str] = Field(
        None,
        description="Free-text creative brief / instructions from the requester",
    )
    assets: List[SubmittedAsset] = Field(
        default_factory=list,
        description="Any images/files attached to the submission",
    )
    raw_fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Untyped extra fields captured verbatim from the source",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Source/debug context (channel, ip, etc.)",
    )
    provenance: Optional[ContractProvenance] = Field(
        None,
        description="Lineage/provenance for this contract instance",
    )


# ---------------------------------------------------------------------------
# Stage 2: Normalization
# ---------------------------------------------------------------------------


class DesignJob(BaseModel):
    """
    NORMALIZATION. The cleaned, structured interpretation of a RawSubmission.

    DesignJob captures *intent and content* in a typed form. It does NOT define
    production requirements (resolution, bleed, color profile) — those are
    resolved downstream into PrintSpecification.
    """

    job_id: str = Field(..., description="Unique id for the normalized job")
    submission_id: str = Field(..., description="RawSubmission this job derived from")
    product_type: ProductType = Field(..., description="Resolved product type")
    title: Optional[str] = Field(None, description="Short normalized title for the job")
    normalized_brief: str = Field(
        ...,
        description="Cleaned, structured restatement of the creative brief",
    )
    requested_quantity: int = Field(1, ge=1, description="Number of copies requested")
    assets: List[SubmittedAsset] = Field(
        default_factory=list,
        description="Normalized references to submission assets",
    )
    attributes: Dict[str, Any] = Field(
        default_factory=dict,
        description="Typed design attributes extracted during normalization",
    )
    provenance: Optional[ContractProvenance] = Field(
        None,
        description="Lineage/provenance for this contract instance",
    )


class NormalizationResult(BaseModel):
    """Result wrapper around the normalization stage."""

    status: ResultStatus = Field(..., description="Outcome of normalization")
    design_job: Optional[DesignJob] = Field(
        None,
        description="Normalized job, present when normalization succeeded",
    )
    reasons: List[StageIssue] = Field(
        default_factory=list,
        description="Why normalization reached this status",
    )
    warnings: List[StageIssue] = Field(
        default_factory=list,
        description="Non-blocking concerns surfaced during normalization",
    )
    next_steps: Optional[str] = Field(
        None,
        description="Guidance for what should happen next",
    )
    provenance: Optional[ContractProvenance] = Field(
        None,
        description="Lineage/provenance for this contract instance",
    )


# ---------------------------------------------------------------------------
# Stage 3: Specification (resolved downstream — NOT normalization)
# ---------------------------------------------------------------------------


class ColorRequirement(BaseModel):
    """Color/profile requirements for production."""

    color_mode: str = Field(..., description="Required color mode, e.g. CMYK, sRGB")
    color_profile: Optional[str] = Field(
        None,
        description="Specific ICC profile name, if required",
    )
    max_ink_coverage_pct: Optional[float] = Field(
        None,
        description="Maximum total ink coverage allowed, if applicable",
    )


class DimensionRequirement(BaseModel):
    """Physical dimension and resolution requirements for production."""

    width_mm: float = Field(..., gt=0, description="Trim width in millimeters")
    height_mm: float = Field(..., gt=0, description="Trim height in millimeters")
    bleed_mm: float = Field(0.0, ge=0, description="Required bleed margin in millimeters")
    safe_margin_mm: float = Field(0.0, ge=0, description="Safe area margin in millimeters")
    min_dpi: int = Field(300, gt=0, description="Minimum required resolution in DPI")


class PrintSpecification(BaseModel):
    """
    PRODUCTION REQUIREMENTS. Resolved downstream from DesignJob + config.

    IMPORTANT: This is NOT a normalization artifact. It represents the concrete
    production requirements (dimensions, bleed, dpi, color, formats) that a
    DesignJob must ultimately satisfy. It is derived from the DesignJob's
    product_type combined with print-shop configuration/policy.
    """

    spec_id: str = Field(..., description="Unique id for this resolved specification")
    job_id: str = Field(..., description="DesignJob this specification was resolved for")
    product_type: ProductType = Field(..., description="Product these requirements apply to")
    dimensions: DimensionRequirement = Field(..., description="Physical/resolution requirements")
    color: ColorRequirement = Field(..., description="Color/profile requirements")
    accepted_formats: List[str] = Field(
        default_factory=list,
        description="Allowed output file formats, e.g. ['pdf', 'tiff']",
    )
    required_asset_roles: List[AssetRole] = Field(
        default_factory=list,
        description="Asset roles that must be present for production",
    )
    config_source: Optional[str] = Field(
        None,
        description="Identifier of the config/policy version used to resolve this spec",
    )
    constraints: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional product/shop-specific constraints",
    )
    provenance: Optional[ContractProvenance] = Field(
        None,
        description="Lineage/provenance for this contract instance",
    )


# ---------------------------------------------------------------------------
# Stage 4: Compliance
# ---------------------------------------------------------------------------


class ComplianceFinding(BaseModel):
    """A single measured gap between a submitted asset and the spec."""

    asset_id: Optional[str] = Field(None, description="Asset this finding refers to")
    requirement: str = Field(..., description="Requirement being checked, e.g. min_dpi")
    expected: Any = Field(None, description="Required value from the PrintSpecification")
    actual: Any = Field(None, description="Observed value from the submitted asset")
    severity: FailureSeverity = Field(
        FailureSeverity.WARNING,
        description="Severity of the gap",
    )
    compliant: bool = Field(..., description="Whether this requirement is satisfied")


class ComplianceResult(BaseModel):
    """
    COMPLIANCE. Measures submitted-image readiness against a PrintSpecification.

    This answers: "Do the submitted assets already meet production requirements,
    and if not, where are the gaps?"
    """

    status: ResultStatus = Field(..., description="Overall compliance outcome")
    spec_id: str = Field(..., description="PrintSpecification measured against")
    job_id: str = Field(..., description="DesignJob under evaluation")
    is_print_ready: bool = Field(
        ...,
        description="True if submitted assets already satisfy the spec",
    )
    findings: List[ComplianceFinding] = Field(
        default_factory=list,
        description="Per-requirement measurements",
    )
    reasons: List[StageIssue] = Field(
        default_factory=list,
        description="Why compliance reached this status",
    )
    warnings: List[StageIssue] = Field(
        default_factory=list,
        description="Non-blocking compliance concerns",
    )
    next_steps: Optional[str] = Field(
        None,
        description="Guidance, e.g. 'adaptation required' or 'ready for production'",
    )
    provenance: Optional[ContractProvenance] = Field(
        None,
        description="Lineage/provenance for this contract instance",
    )


# ---------------------------------------------------------------------------
# Stage 5: Adaptation (deterministic transformation intent)
# ---------------------------------------------------------------------------


class TransformationStep(BaseModel):
    """A single deterministic transformation to apply."""

    transformation: TransformationType = Field(..., description="What kind of transform")
    target_asset_role: AssetRole = Field(
        AssetRole.PRIMARY,
        description="Which asset role this step applies to",
    )
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Deterministic parameters for the transform",
    )
    reason: Optional[str] = Field(
        None,
        description="Which compliance gap this step addresses",
    )


class AdaptationPlan(BaseModel):
    """
    ADAPTATION. Defines deterministic transformation intent.

    This is a plan only — an ordered, reviewable list of transformations that
    would bring submitted assets into compliance. It performs no side effects.
    """

    plan_id: str = Field(..., description="Unique id for this plan")
    spec_id: str = Field(..., description="PrintSpecification the plan targets")
    job_id: str = Field(..., description="DesignJob the plan applies to")
    status: ResultStatus = Field(
        ResultStatus.PENDING,
        description="Outcome of planning (PASSED when a plan exists, SKIPPED when none needed)",
    )
    requires_generation: bool = Field(
        False,
        description="True if generation is needed (deterministic transforms insufficient)",
    )
    steps: List[TransformationStep] = Field(
        default_factory=list,
        description="Ordered, deterministic transformation steps",
    )
    reasons: List[StageIssue] = Field(
        default_factory=list,
        description="Why these transformations were chosen",
    )
    warnings: List[StageIssue] = Field(
        default_factory=list,
        description="Risks/limitations of the plan",
    )
    next_steps: Optional[str] = Field(
        None,
        description="Human-readable guidance on what should happen next",
    )
    provenance: Optional[ContractProvenance] = Field(
        None,
        description="Lineage/provenance for this contract instance",
    )


# ---------------------------------------------------------------------------
# Stage 6: Generation
# ---------------------------------------------------------------------------


class GenerationRequest(BaseModel):
    """
    GENERATION input. Strict and model-ready — not loose prompt text only.

    Carries fully-resolved generation parameters so a model call is reproducible
    and constrained by the PrintSpecification, not just a free-text prompt.
    """

    request_id: str = Field(..., description="Unique id for this generation request")
    spec_id: str = Field(..., description="PrintSpecification constraining the output")
    job_id: str = Field(..., description="DesignJob being fulfilled")
    plan_id: Optional[str] = Field(
        None,
        description="AdaptationPlan that triggered generation, if any",
    )
    prompt: str = Field(..., description="Structured generation prompt")
    negative_prompt: Optional[str] = Field(
        None,
        description="Things to avoid in the output",
    )
    output_width_px: int = Field(..., gt=0, description="Required output width in pixels")
    output_height_px: int = Field(..., gt=0, description="Required output height in pixels")
    target_dpi: int = Field(300, gt=0, description="Target DPI derived from the spec")
    color_mode: str = Field(..., description="Required color mode, e.g. CMYK, sRGB")
    output_format: str = Field(..., description="Required output file format")
    seed: Optional[int] = Field(None, description="Seed for reproducibility, if supported")
    candidate_count: int = Field(1, ge=1, description="How many candidates to request")
    reference_asset_ids: List[str] = Field(
        default_factory=list,
        description="Submitted assets used as references/conditioning",
    )
    generation_parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional model-specific generation parameters",
    )
    provenance: Optional[ContractProvenance] = Field(
        None,
        description="Lineage/provenance for this contract instance",
    )


class GeneratedCandidate(BaseModel):
    """
    GENERATION output. Represents a single model output candidate.
    """

    candidate_id: str = Field(..., description="Unique id for this candidate")
    request_id: str = Field(..., description="GenerationRequest that produced it")
    uri: str = Field(..., description="Location/reference of the generated asset")
    properties: ImageProperties = Field(
        default_factory=ImageProperties,
        description="Observed properties of the generated output",
    )
    seed: Optional[int] = Field(None, description="Seed actually used, if reported")
    score: Optional[float] = Field(
        None,
        description="Optional model/aesthetic score for ranking candidates",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Model/provider metadata for debugging",
    )
    provenance: Optional[ContractProvenance] = Field(
        None,
        description="Lineage/provenance for this contract instance",
    )


# ---------------------------------------------------------------------------
# Stage 7: Validation
# ---------------------------------------------------------------------------


class ValidationResult(BaseModel):
    """
    VALIDATION. Checks generated outputs against the spec before approval.

    This is the automated gate that runs *before* a human is asked to approve.
    """

    status: ResultStatus = Field(..., description="Overall validation outcome")
    spec_id: str = Field(..., description="PrintSpecification validated against")
    request_id: Optional[str] = Field(
        None,
        description="GenerationRequest whose candidates were validated",
    )
    validated_candidate_ids: List[str] = Field(
        default_factory=list,
        description="Candidates that were evaluated",
    )
    passed_candidate_ids: List[str] = Field(
        default_factory=list,
        description="Candidates that passed validation",
    )
    findings: List[ComplianceFinding] = Field(
        default_factory=list,
        description="Per-candidate spec measurements",
    )
    reasons: List[StageIssue] = Field(
        default_factory=list,
        description="Why validation reached this status",
    )
    warnings: List[StageIssue] = Field(
        default_factory=list,
        description="Non-blocking validation concerns",
    )
    next_steps: Optional[str] = Field(
        None,
        description="Guidance, e.g. 'route to approval' or 'regenerate'",
    )
    provenance: Optional[ContractProvenance] = Field(
        None,
        description="Lineage/provenance for this contract instance",
    )


# ---------------------------------------------------------------------------
# AI Generation Service: model invocation record
# ---------------------------------------------------------------------------


class ModelInvocationRecord(BaseModel):
    """
    A single model invocation by the AI Generation Service.

    The AI acts here as an *actuator*: it executes a GenerationRequest and
    records what happened (provider, model, timing, cost, outputs, errors).
    It does not decide control flow — that remains with the orchestrator.
    """

    invocation_id: str = Field(..., description="Unique id for this invocation")
    request_id: str = Field(..., description="GenerationRequest this invocation served")
    provider: str = Field(..., description="Model provider, e.g. openai, stability, replicate")
    model_name: str = Field(..., description="Concrete model identifier invoked")
    status: InvocationStatus = Field(..., description="Outcome of the invocation")
    started_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the invocation started",
    )
    completed_at: Optional[datetime] = Field(
        None,
        description="When the invocation finished, if it has",
    )
    latency_ms: Optional[int] = Field(
        None,
        ge=0,
        description="End-to-end invocation latency in milliseconds",
    )
    generated_candidate_ids: List[str] = Field(
        default_factory=list,
        description="GeneratedCandidate ids produced by this invocation",
    )
    error_code: Optional[str] = Field(
        None,
        description="Machine-readable error code if the invocation failed",
    )
    error_message: Optional[str] = Field(
        None,
        description="Human-readable error detail if the invocation failed",
    )
    retry_count: int = Field(0, ge=0, description="Number of retries performed")
    usage: Dict[str, Any] = Field(
        default_factory=dict,
        description="Provider usage metrics, e.g. tokens/images/credits",
    )
    cost_estimate: Optional[float] = Field(
        None,
        ge=0,
        description="Estimated cost of this invocation, if known",
    )
    provenance: Optional[ContractProvenance] = Field(
        None,
        description="Lineage/provenance for this contract instance",
    )


# ---------------------------------------------------------------------------
# Stage 8: Approval
# ---------------------------------------------------------------------------


class ApprovalPackage(BaseModel):
    """
    APPROVAL (Approval Workflow Service). The bundle routed to a human reviewer.

    Assembled after validation passes: it gathers the validated candidates and
    context so an owner/reviewer group can make an ApprovalDecision. This is the
    request-for-decision; ApprovalDecision is the recorded outcome.
    """

    package_id: str = Field(..., description="Unique id for this approval package")
    spec_id: str = Field(..., description="PrintSpecification in context")
    job_id: str = Field(..., description="DesignJob in context")
    candidate_ids: List[str] = Field(
        default_factory=list,
        description="Validated candidates presented for approval",
    )
    validation_reference: Optional[str] = Field(
        None,
        description="ValidationResult id / reference backing this package",
    )
    status: ResultStatus = Field(
        ResultStatus.PENDING,
        description="Packaging/routing status of the approval request",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the approval package was assembled",
    )
    routed_to: Optional[str] = Field(
        None,
        description="Reviewer group / owner the package was routed to",
    )
    reasons: List[StageIssue] = Field(
        default_factory=list,
        description="Why this package was assembled/routed as it was",
    )
    warnings: List[StageIssue] = Field(
        default_factory=list,
        description="Non-blocking concerns for the reviewer",
    )
    next_steps: Optional[str] = Field(
        None,
        description="Guidance, e.g. 'awaiting owner review'",
    )
    provenance: Optional[ContractProvenance] = Field(
        None,
        description="Lineage/provenance for this contract instance",
    )


class ApprovalDecision(BaseModel):
    """
    APPROVAL. Records a human decision on a validated candidate.
    """

    decision_id: str = Field(..., description="Unique id for this approval decision")
    spec_id: str = Field(..., description="PrintSpecification in context")
    candidate_id: Optional[str] = Field(
        None,
        description="Candidate the decision applies to (if one was chosen)",
    )
    status: ApprovalStatus = Field(..., description="Human approval outcome")
    approver: Optional[str] = Field(
        None,
        description="Who made the decision (user id/email)",
    )
    decided_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the decision was made",
    )
    reasons: List[StageIssue] = Field(
        default_factory=list,
        description="Rationale for the decision",
    )
    next_steps: Optional[str] = Field(
        None,
        description="Guidance, e.g. 'package for production' or 'request changes'",
    )
    provenance: Optional[ContractProvenance] = Field(
        None,
        description="Lineage/provenance for this contract instance",
    )


# ---------------------------------------------------------------------------
# Stage 9: Production
# ---------------------------------------------------------------------------


class ProductionPackage(BaseModel):
    """
    PRODUCTION. The final, approved output bundle ready for the print shop.
    """

    package_id: str = Field(..., description="Unique id for the production package")
    spec_id: str = Field(..., description="PrintSpecification satisfied by this package")
    job_id: str = Field(..., description="DesignJob this package fulfills")
    candidate_id: Optional[str] = Field(
        None,
        description="Approved candidate included in the package",
    )
    decision_id: Optional[str] = Field(
        None,
        description="ApprovalDecision authorizing production",
    )
    status: ResultStatus = Field(
        ResultStatus.PASSED,
        description="Packaging outcome",
    )
    output_uris: List[str] = Field(
        default_factory=list,
        description="Final production-ready asset locations",
    )
    manifest: Dict[str, Any] = Field(
        default_factory=dict,
        description="Production manifest (formats, color, dimensions actually shipped)",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the package was assembled",
    )
    reasons: List[StageIssue] = Field(
        default_factory=list,
        description="Notes about the packaging outcome",
    )
    warnings: List[StageIssue] = Field(
        default_factory=list,
        description="Non-blocking concerns about the package",
    )
    provenance: Optional[ContractProvenance] = Field(
        None,
        description="Lineage/provenance for this contract instance",
    )


# ---------------------------------------------------------------------------
# Orchestration bundle
# ---------------------------------------------------------------------------


class PrintWorkflowRunResult(BaseModel):
    """
    Bundles stage outputs for orchestration logs / debug output.

    This is the end-to-end record of a single print-specification run. All stage
    outputs are optional so partial runs (and failures) are representable.
    """

    run_id: str = Field(..., description="Unique id for this workflow run")
    submission_id: str = Field(..., description="RawSubmission that started the run")
    stage: PrintWorkflowStage = Field(..., description="Coarse workflow stage (phase)")
    state: PrintWorkflowState = Field(..., description="Fine-grained state-machine state")
    status: ResultStatus = Field(..., description="Overall run outcome")
    started_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the run started",
    )
    completed_at: Optional[datetime] = Field(
        None,
        description="When the run finished, if it has",
    )

    # Stage outputs (populated as the run progresses)
    raw_submission: Optional[RawSubmission] = Field(None, description="Intake output")
    normalization: Optional[NormalizationResult] = Field(
        None, description="Normalization output"
    )
    specification: Optional[PrintSpecification] = Field(
        None, description="Resolved specification"
    )
    compliance: Optional[ComplianceResult] = Field(None, description="Compliance output")
    adaptation: Optional[AdaptationPlan] = Field(None, description="Adaptation plan")
    generation_request: Optional[GenerationRequest] = Field(
        None, description="Generation request"
    )
    candidates: List[GeneratedCandidate] = Field(
        default_factory=list, description="Generated candidates"
    )
    model_invocations: List[ModelInvocationRecord] = Field(
        default_factory=list,
        description="AI Generation Service invocation records for this run",
    )
    validation: Optional[ValidationResult] = Field(None, description="Validation output")
    approval_package: Optional[ApprovalPackage] = Field(
        None, description="Approval package routed for review"
    )
    approval: Optional[ApprovalDecision] = Field(None, description="Approval decision")
    production_package: Optional[ProductionPackage] = Field(
        None, description="Final production package"
    )

    reasons: List[StageIssue] = Field(
        default_factory=list,
        description="Run-level reasons (e.g. why it failed/stopped)",
    )
    warnings: List[StageIssue] = Field(
        default_factory=list,
        description="Run-level non-blocking concerns",
    )
    next_steps: Optional[str] = Field(
        None,
        description="What an operator/caller should do next",
    )
    provenance: Optional[ContractProvenance] = Field(
        None,
        description="Lineage/provenance for this contract instance",
    )
