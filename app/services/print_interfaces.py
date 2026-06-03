"""
Typed subsystem interfaces for the print-specification workflow.

These are structural contracts (typing.Protocol) describing *what each subsystem
must provide*, expressed purely in terms of the domain contracts in
app/domain/print_schemas.py. There are NO implementations here.

Design rules reflected by these interfaces:
  - Each subsystem owns a narrow slice of the pipeline and produces specific contracts.
  - Normalization produces DesignJob/NormalizationResult only — never PrintSpecification.
  - PrintSpecification is resolved downstream from DesignJob + config.
  - The AI Generation service is an actuator: it executes a GenerationRequest and
    records what happened; it does not decide control flow.
  - Orchestration (sequencing/state transitions) lives elsewhere, not in these
    subsystems.

This module performs no I/O, makes no model calls, imports no web framework, and
contains no orchestration logic. Method bodies are intentionally empty (`...`).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from app.domain.print_schemas import (
    AdaptationPlan,
    ApprovalDecision,
    ApprovalPackage,
    ApprovalStatus,
    ComplianceResult,
    DesignJob,
    GeneratedCandidate,
    GenerationRequest,
    ImageProperties,
    ModelInvocationRecord,
    NormalizationResult,
    PrintSpecification,
    PrintWorkflowState,
    ProductionPackage,
    RawSubmission,
    ValidationResult,
)


@runtime_checkable
class SubmissionIntakeService(Protocol):
    """
    Owns: intake of raw, untrusted input into a RawSubmission.

    Must not: interpret/clean the brief, infer ProductType, resolve any
    production requirements, or reject on content quality. Intake stays faithful
    to what was received.
    """

    def intake_submission(self, raw_input: Dict[str, Any]) -> RawSubmission:
        """Capture external input verbatim as a RawSubmission."""
        ...


@runtime_checkable
class NormalizationService(Protocol):
    """
    Owns: DesignJob and NormalizationResult.

    Must not: produce PrintSpecification or set any production requirement
    (dimensions, bleed, DPI, color profile). It captures typed intent/content only.
    """

    def normalize_submission(self, raw_submission: RawSubmission) -> NormalizationResult:
        """Normalize a RawSubmission into a NormalizationResult (carrying a DesignJob)."""
        ...


@runtime_checkable
class SpecificationResolutionService(Protocol):
    """
    Owns: PrintSpecification (resolved downstream from DesignJob + config).

    Must not: re-derive design intent, measure submitted assets, or run as part
    of normalization. Spec is strictly downstream of DesignJob + configuration.
    """

    def resolve_specification(self, design_job: DesignJob) -> PrintSpecification:
        """Resolve production requirements for a DesignJob into a PrintSpecification."""
        ...


@runtime_checkable
class TechnicalComplianceService(Protocol):
    """
    Owns: ComplianceResult.

    Must not: transform/fix assets, plan adaptations, or invoke generation. It
    only measures submitted-image readiness against the specification.
    """

    def evaluate_compliance(
        self,
        design_job: DesignJob,
        specification: PrintSpecification,
        image_properties: ImageProperties,
    ) -> ComplianceResult:
        """Measure submitted image properties against the specification."""
        ...


@runtime_checkable
class AdaptationPlanningService(Protocol):
    """
    Owns: AdaptationPlan.

    Must not: execute transformations or call any model. It defines deterministic
    transformation intent only — a reviewable plan with no side effects.
    """

    def create_adaptation_plan(
        self,
        design_job: DesignJob,
        specification: PrintSpecification,
        compliance_result: ComplianceResult,
    ) -> AdaptationPlan:
        """Produce a deterministic AdaptationPlan addressing compliance gaps."""
        ...


@runtime_checkable
class PromptConstructionService(Protocol):
    """
    Owns: assembly of GenerationRequest.

    Must not: call the model, emit loose prompt text only, or relax spec
    constraints. It builds a strict, model-ready request derived from the spec.
    """

    def build_generation_request(
        self,
        design_job: DesignJob,
        specification: PrintSpecification,
        adaptation_plan: AdaptationPlan,
    ) -> GenerationRequest:
        """Construct a strict, spec-constrained GenerationRequest."""
        ...


@runtime_checkable
class AIGenerationService(Protocol):
    """
    Owns: GeneratedCandidate outputs and ModelInvocationRecord.

    Must not: decide control flow, validate its own output, approve, or package.
    It is an actuator: execute the request and record provider/model/timing/cost.
    """

    def generate_candidates(
        self,
        generation_request: GenerationRequest,
    ) -> Tuple[List[GeneratedCandidate], List[ModelInvocationRecord]]:
        """Execute a GenerationRequest, returning candidates and invocation records."""
        ...


@runtime_checkable
class OutputValidationService(Protocol):
    """
    Owns: ValidationResult.

    Must not: approve, request human review directly, or package. It is the
    automated gate that runs before approval.
    """

    def validate_candidates(
        self,
        candidates: List[GeneratedCandidate],
        specification: PrintSpecification,
        adaptation_plan: AdaptationPlan,
    ) -> ValidationResult:
        """Validate generated candidates against the spec and adaptation plan."""
        ...


@runtime_checkable
class ApprovalWorkflowService(Protocol):
    """
    Owns: ApprovalPackage (request-for-decision) and ApprovalDecision (outcome).

    Must not: auto-approve, generate/transform assets, or build the production
    package. The decision is human; the subsystem only routes and records.
    """

    def create_approval_package(
        self,
        specification: PrintSpecification,
        design_job: DesignJob,
        validation_result: ValidationResult,
    ) -> ApprovalPackage:
        """Assemble validated candidates into an ApprovalPackage routed for review."""
        ...

    def record_approval_decision(
        self,
        approval_package: ApprovalPackage,
        status: ApprovalStatus,
        approver: Optional[str] = None,
    ) -> ApprovalDecision:
        """Record a human ApprovalDecision against an ApprovalPackage."""
        ...


@runtime_checkable
class ProductionPackagingService(Protocol):
    """
    Owns: ProductionPackage.

    Must not: re-validate, re-generate, or alter the approved candidate. It
    assembles the final production-ready bundle for the print shop.
    """

    def create_production_package(
        self,
        approval_decision: ApprovalDecision,
        approved_candidate: GeneratedCandidate,
        specification: PrintSpecification,
    ) -> ProductionPackage:
        """Assemble the final ProductionPackage from an approved candidate."""
        ...


@runtime_checkable
class AuditObservationService(Protocol):
    """
    Owns: nothing (no domain contracts). Read-only observation.

    Must not: mutate any contract, change state, or influence control flow.
    It records events derived from contracts/state for logs and reporting.
    """

    def record_event(self, event_name: str, fields: Dict[str, Any]) -> None:
        """Emit a structured observation event (no mutation, no control flow)."""
        ...

    def record_transition(
        self,
        run_id: str,
        from_state: PrintWorkflowState,
        to_state: PrintWorkflowState,
    ) -> None:
        """Observe a state transition for audit purposes."""
        ...


@runtime_checkable
class StorageAssetService(Protocol):
    """
    Owns: nothing semantically — stored asset bytes and resolvable URIs only.

    Must not: interpret/validate/transform asset content or make routing
    decisions. It stores assets only.
    """

    def store_asset(self, asset_id: str, data: bytes) -> str:
        """Persist asset bytes and return a resolvable URI."""
        ...

    def resolve_asset(self, uri: str) -> bytes:
        """Resolve a previously stored asset URI back to its bytes."""
        ...


@runtime_checkable
class ConfigurationService(Protocol):
    """
    Owns: rules/policy inputs (e.g. spec resolution rules) and config_version.

    Must not: hold runtime state, perform subsystem work, or make per-run
    decisions. It supplies rules only; decisions belong to the consuming subsystem.
    """

    def get_config_version(self) -> str:
        """Return the active configuration/policy version identifier."""
        ...

    def get_specification_rules(self, product_type: str) -> Dict[str, Any]:
        """Return the production-requirement rules for a given product type."""
        ...
