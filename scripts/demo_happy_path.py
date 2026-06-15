"""
Stakeholder-facing demonstration of the completed print workflow.

Runs a weekend car-detailing banner job from customer submission through
production readiness — console output only. No OpenAI, no web server, no database.

    python scripts/demo_happy_path.py
"""

from __future__ import annotations

import math
import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.domain.print_orchestration_schemas import WorkflowAdvanceRequest, WorkflowOperation
from app.domain.print_schemas import (
    ApprovalStatus,
    AssetRole,
    GeneratedCandidate,
    ImageProperties,
    InvocationStatus,
    ModelInvocationRecord,
    PrintWorkflowState,
    RawSubmission,
    ResultStatus,
    SubmittedAsset,
)
from app.services import print_orchestrator
from app.services.print_orchestrator import advance_workflow

S = PrintWorkflowState

OPTION_LABEL = "Option 1"
PACKAGE_LABEL = "Production Package 1"


def _print_intro() -> None:
    print("=" * 50)
    print("PRINT WORKFLOW AUTOMATION DEMO")
    print("=" * 50)
    print()
    print("=" * 50)
    print("WHAT THIS SYSTEM DOES")
    print("=" * 50)
    print()
    print("What the system helps with:")
    print("  - Checking files against your shop rules")
    print("  - Identifying production issues")
    print("  - Creating a repair plan")
    print("  - Validating corrected output")
    print("  - Tracking approvals")
    print("  - Preparing production handoff")
    print()
    print("What the system does NOT replace:")
    print("  - Your expertise")
    print("  - Your customer relationships")
    print("  - Your final approval authority")
    print("  - Your production judgment")
    print()
    print(
        "The goal is not to replace people.\n"
        "The goal is to reduce repetitive technical work."
    )
    print()
    print("Example job: Weekend car-detailing promotion banner")
    print()


def _friendly_state(state: PrintWorkflowState) -> str:
    labels = {
        S.NORMALIZED: "Request understood",
        S.SPECIFICATION_RESOLVED: "Print requirements identified",
        S.COMPLIANCE_COMPLETE: "File checked against requirements",
        S.ADAPTATION_PLANNED: "Repair plan created",
        S.GENERATION_PENDING: "Preparing corrected output",
        S.GENERATION_COMPLETE: "Corrected options produced",
        S.VALIDATION_COMPLETE: "Output validated",
        S.OWNER_REVIEW_PENDING: "Awaiting owner approval",
        S.APPROVED: "Owner approved",
        S.PRODUCTION_PACKAGE_CREATED: "Production package created",
        S.COMPLETED: "Workflow complete",
    }
    return labels.get(state, state.value.replace("_", " ").title())


def _banner_submission() -> RawSubmission:
    submission_id = str(uuid.uuid4())
    asset = SubmittedAsset(
        asset_id=str(uuid.uuid4()),
        role=AssetRole.PRIMARY,
        uri="file:///customer-uploads/weekend-detailing-banner.pdf",
        properties=ImageProperties(
            width_px=1200,
            height_px=600,
            dpi=72,
            file_format="pdf",
            color_profile="srgb",
        ),
    )
    return RawSubmission(
        submission_id=submission_id,
        requester="frontdesk@precisiondetail.example",
        requested_product="banner",
        brief=(
            "Weekend car detailing special for our shop. Bold headline, clean "
            "automotive look, premium local business feel. Needed for Saturday promotion."
        ),
        assets=[asset],
    )


def _required_px(width_mm: float, height_mm: float, min_dpi: int) -> tuple[int, int]:
    w = math.ceil(width_mm / 25.4 * min_dpi) + 100
    h = math.ceil(height_mm / 25.4 * min_dpi) + 100
    return w, h


def _compliant_image(spec) -> ImageProperties:
    w, h = _required_px(
        spec.dimensions.width_mm,
        spec.dimensions.height_mm,
        spec.dimensions.min_dpi,
    )
    return ImageProperties(
        width_px=w,
        height_px=h,
        dpi=spec.dimensions.min_dpi,
        color_profile=spec.color.color_profile or spec.color.color_mode,
        file_format=spec.accepted_formats[0],
    )


def _stub_generation_outputs(generation_request, spec):
    candidate = GeneratedCandidate(
        candidate_id=f"candidate-{generation_request.request_id}-001",
        request_id=generation_request.request_id,
        uri=f"artifact://generated/candidate-{generation_request.request_id}-001.png",
        properties=_compliant_image(spec),
    )
    invocation = ModelInvocationRecord(
        invocation_id=f"invocation-{generation_request.request_id}",
        request_id=generation_request.request_id,
        provider="local-demo",
        model_name="deterministic-demo-generator",
        status=InvocationStatus.SUCCEEDED,
        generated_candidate_ids=[candidate.candidate_id],
    )
    return [candidate], [invocation]


def _advance(current_state, *, run_result=None, raw_submission=None, metadata=None):
    return advance_workflow(
        WorkflowAdvanceRequest(
            run_id="demo-weekend-detailing",
            idempotency_key=f"demo-{current_state.value}",
            operation=WorkflowOperation.ADVANCE,
            current_state=current_state,
            requested_target_state=None,
            raw_submission=raw_submission,
            existing_run_result=run_result,
            metadata=metadata or {},
        )
    )


def _section(number: int, title: str, explanation: str, state: PrintWorkflowState, details: list[str]) -> None:
    print(f"[{number}] {title}")
    print("-" * 50)
    print(explanation)
    print()
    print(f"Current workflow step: {_friendly_state(state)}")
    if details:
        print("Important details:")
        for line in details:
            print(f"  - {line}")
    print()


def _finding_summary(requirement: str) -> str:
    summaries = {
        "min_dpi": "Resolution is too low for a large banner print",
        "width_px": "Image width does not meet banner size requirements",
        "height_px": "Image height does not meet banner size requirements",
        "file_format": "File type may need adjustment for production",
        "color_profile": "Color setup does not match shop print standards",
    }
    return summaries.get(requirement, f"Issue with {requirement.replace('_', ' ')}")


def main() -> None:
    _print_intro()

    spec_holder: list = []
    original_generate = print_orchestrator.generate_candidates

    def _fake_generate(req):
        return _stub_generation_outputs(req, spec_holder[0])

    print_orchestrator.generate_candidates = _fake_generate

    try:
        submission = _banner_submission()

        # --- Step 1: Normalization ---
        result = _advance(S.NORMALIZATION_PENDING, raw_submission=submission)
        run = result.run_result
        job = run.normalization.design_job

        _section(
            1,
            "Customer Request Received",
            "A customer submits artwork and a short description for a weekend "
            "car-detailing banner. The system organizes the request so every "
            "following step uses the same job information.",
            result.current_state,
            [
                f"Customer message: {submission.brief[:80]}...",
                f"Product requested: {job.product_type.value.replace('_', ' ').title()}",
                "Job title: Weekend car detailing banner",
                f"Submitted by: {submission.requester}",
            ],
        )

        # --- Step 2: Specification ---
        result = _advance(S.NORMALIZED, run_result=run)
        run = result.run_result
        spec = run.specification
        spec_holder.append(spec)

        _section(
            2,
            "Print Requirements Identified",
            "Before inspecting the file in detail, the system applies your "
            "shop's standard rules for this product: size, resolution, color, "
            "and accepted file types.",
            result.current_state,
            [
                f"Banner size: {spec.dimensions.width_mm:.0f} mm x {spec.dimensions.height_mm:.0f} mm",
                f"Minimum resolution: {spec.dimensions.min_dpi} DPI",
                f"Color mode: {spec.color.color_mode}",
                f"Accepted file types: {', '.join(spec.accepted_formats)}",
            ],
        )

        # --- Step 3: Compliance ---
        result = _advance(S.SPECIFICATION_RESOLVED, run_result=run)
        run = result.run_result
        compliance = run.compliance

        compliance_details = [
            f"Print-ready right now: {'Yes' if compliance.is_print_ready else 'No'}",
        ]
        if compliance.findings:
            compliance_details.append("Issues found:")
            for finding in compliance.findings[:5]:
                compliance_details.append(
                    f"    {_finding_summary(finding.requirement)}"
                )
        else:
            compliance_details.append("No blocking issues reported.")

        _section(
            3,
            "File Checked Against Requirements",
            "The submitted file is compared to your banner standards. The system "
            "reports specific issues, not a vague 'this looks wrong.'",
            result.current_state,
            compliance_details,
        )

        # --- Step 4: Adaptation plan ---
        result = _advance(S.COMPLIANCE_COMPLETE, run_result=run)
        run = result.run_result
        plan = run.adaptation

        plan_details = []
        if plan and plan.steps:
            for step in plan.steps:
                req = (step.parameters or {}).get("requirement", "")
                plan_details.append(_finding_summary(str(req)))
        if plan:
            if plan.requires_generation:
                plan_details.append(
                    "A new high-quality version must be produced to meet banner "
                    "standards (demo uses local generation only; no OpenAI)."
                )
            else:
                plan_details.append("Standard automated repairs are sufficient.")

        _section(
            4,
            "Repair Plan Created",
            "Instead of an employee guessing the fix, the system writes a clear "
            "repair plan. AI is only considered when the plan says it is necessary.",
            result.current_state,
            plan_details or ["No repair steps required."],
        )

        # --- Step 5: Generation ---
        result = _advance(S.ADAPTATION_PLANNED, run_result=run)
        run = result.run_result

        result = _advance(S.GENERATION_PENDING, run_result=run)
        run = result.run_result
        candidate = run.candidates[0] if run.candidates else None

        gen_details = []
        if candidate:
            gen_details.append(f"Corrected option prepared: {OPTION_LABEL}")
            if candidate.properties and candidate.properties.dpi:
                gen_details.append(
                    f"Output resolution: {candidate.properties.dpi} DPI"
                )
            if candidate.properties and candidate.properties.width_px:
                gen_details.append(
                    f"Output size: {candidate.properties.width_px} x "
                    f"{candidate.properties.height_px} pixels"
                )
        gen_details.append("No live AI service was called in this demo.")

        _section(
            5,
            "Corrected Options Produced",
            "The system produces a corrected candidate ready for validation. "
            "When AI is required in production, it runs only under your shop's "
            "specifications, not as a random image toy.",
            result.current_state,
            gen_details,
        )

        # --- Step 6: Validation ---
        result = _advance(S.GENERATION_COMPLETE, run_result=run)
        run = result.run_result
        validation = run.validation

        val_details = [
            f"Validation result: {validation.status.value.replace('_', ' ').title()}",
            "Checks include size, resolution, file type, and color where available.",
            "This step confirms print readiness, not marketing taste or layout preference.",
        ]

        _section(
            6,
            "Output Validated",
            "Before anyone is asked to approve, the system verifies the corrected "
            "file meets technical print requirements.",
            result.current_state,
            val_details,
        )

        # --- Step 7: Owner approval ---
        result = _advance(S.VALIDATION_COMPLETE, run_result=run)
        run = result.run_result

        candidate_id = run.candidates[0].candidate_id
        result = _advance(
            S.OWNER_REVIEW_PENDING,
            run_result=run,
            metadata={
                "approval_decision": {
                    "status": ApprovalStatus.APPROVED.value,
                    "candidate_id": candidate_id,
                    "approver": "shop-owner@precisiondetail.example",
                }
            },
        )
        run = result.run_result
        approval = run.approval

        _section(
            7,
            "Owner Approval",
            "A human owner reviews the validated option and makes the final call. "
            "Nothing moves to production packaging without explicit approval.",
            result.current_state,
            [
                f"Decision: {approval.status.value.replace('_', ' ').title()}",
                f"Approved option: {OPTION_LABEL}",
                f"Approved by: {approval.approver}",
            ],
        )

        # --- Step 8: Production package ---
        result = _advance(S.APPROVED, run_result=run)
        run = result.run_result
        package = run.production_package
        manifest = package.manifest if package else {}

        pkg_details = []
        if package:
            pkg_details.append(f"Package reference: {PACKAGE_LABEL}")
            if manifest.get("product_type"):
                pkg_details.append(f"Product: {manifest['product_type']}")
            if manifest.get("width_mm") and manifest.get("height_mm"):
                pkg_details.append(
                    f"Dimensions: {manifest['width_mm']} mm x {manifest['height_mm']} mm"
                )
            if manifest.get("min_dpi"):
                pkg_details.append(f"Resolution: {manifest['min_dpi']} DPI")
            if manifest.get("approved_output_id"):
                pkg_details.append(f"Approved output: {OPTION_LABEL}")

        _section(
            8,
            "Production Package Created",
            "The approved file is bundled with a production summary for your "
            "prepress or shop-floor team: one clear handoff.",
            result.current_state,
            pkg_details,
        )

        # --- Step 9: Complete ---
        result = _advance(S.PRODUCTION_PACKAGE_CREATED, run_result=run)
        run = result.run_result

        _section(
            9,
            "Workflow Complete",
            "The job is marked complete. The file has been checked, corrected if "
            "needed, validated, approved, and packaged for production.",
            result.current_state,
            [
                "The job is ready for your print process.",
                "A full deployment would retain this history for audit and support.",
            ],
        )

        # --- Summary ---
        print("=" * 50)
        print("DEMO SUMMARY")
        print("=" * 50)
        print()
        approved_id = approval.candidate_id if approval else candidate_id
        package_id = package.package_id if package else None

        print("Approved Output:")
        print(f"  {OPTION_LABEL}")
        print()
        print("Production Package:")
        print(f"  {PACKAGE_LABEL}")
        if package and package.output_uris:
            print(f"  File ready for production handoff")
        print()
        print("Technical Reference:")
        print(f"  Candidate ID: {approved_id}")
        if package_id:
            print(f"  Package ID: {package_id}")
        if package and package.output_uris:
            print(f"  File location: {package.output_uris[0]}")
        print()
        print("Final Workflow State:")
        print("  COMPLETED")
        print()
        print(
            '"This system is not an image generator.\n\n'
            "It is a workflow system that helps move print jobs from submission "
            'to production readiness in a controlled, auditable way."'
        )
        print()

        if result.current_state != S.COMPLETED:
            print("Demo did not reach COMPLETED. Please report this issue.", file=sys.stderr)
            sys.exit(1)

    finally:
        print_orchestrator.generate_candidates = original_generate


if __name__ == "__main__":
    main()
