import uuid

import pytest

from app.domain.print_schemas import (
    AdaptationPlan,
    ComplianceFinding,
    ComplianceResult,
    DesignJob,
    FailureSeverity,
    ProductType,
    ResultStatus,
    TransformationType,
)
from app.services.print_specification import resolve_specification

# Expected to FAIL until app/services/print_adaptation.py exists. These tests
# define the contract the future implementation must satisfy. They are
# intentionally not skipped/xfailed.
from app.services.print_adaptation import create_adaptation_plan


def _design_job(product_type: ProductType = ProductType.BANNER) -> DesignJob:
    return DesignJob(
        job_id=f"job-{uuid.uuid4()}",
        submission_id=str(uuid.uuid4()),
        product_type=product_type,
        title="Summer sale",
        normalized_brief="A bold outdoor banner advertising a summer sale.",
        requested_quantity=1,
    )


def _compliance(spec, job, *, status, is_print_ready, findings):
    return ComplianceResult(
        status=status,
        spec_id=spec.spec_id,
        job_id=job.job_id,
        is_print_ready=is_print_ready,
        findings=findings,
    )


def _steps_referencing(plan, requirement):
    """Steps whose reason references a given compliance requirement."""
    return [
        s for s in plan.steps if s.reason and requirement in s.reason.lower()
    ]


def test_print_ready_compliance_creates_noop_plan():
    job = _design_job()
    spec = resolve_specification(job)
    compliance = _compliance(
        spec, job, status=ResultStatus.PASSED, is_print_ready=True, findings=[]
    )

    plan = create_adaptation_plan(job, spec, compliance)

    assert plan.status in (ResultStatus.SKIPPED, ResultStatus.PASSED)
    assert plan.requires_generation is False
    assert plan.steps == []
    assert plan.next_steps


def test_low_dpi_creates_upscale_plan():
    job = _design_job()
    spec = resolve_specification(job)
    finding = ComplianceFinding(
        requirement="min_dpi",
        expected=spec.dimensions.min_dpi,
        actual=spec.dimensions.min_dpi - 150,
        compliant=False,
        severity=FailureSeverity.ERROR,
    )
    compliance = _compliance(
        spec,
        job,
        status=ResultStatus.FAILED,
        is_print_ready=False,
        findings=[finding],
    )

    plan = create_adaptation_plan(job, spec, compliance)

    assert plan.status == ResultStatus.PASSED
    assert plan.steps
    # A transformation addressing low DPI must exist (upscale / dpi adjustment).
    dpi_steps = [
        s
        for s in plan.steps
        if s.transformation
        in (TransformationType.UPSCALE, TransformationType.DPI_ADJUSTMENT)
    ]
    assert dpi_steps
    # The step should reference the min_dpi finding.
    assert _steps_referencing(plan, "min_dpi")


def test_bad_file_format_creates_convert_step():
    job = _design_job()
    spec = resolve_specification(job)
    finding = ComplianceFinding(
        requirement="file_format",
        expected=spec.accepted_formats,
        actual="gif",
        compliant=False,
        severity=FailureSeverity.ERROR,
    )
    compliance = _compliance(
        spec,
        job,
        status=ResultStatus.FAILED,
        is_print_ready=False,
        findings=[finding],
    )

    plan = create_adaptation_plan(job, spec, compliance)

    assert plan.steps
    # A step addressing the file_format finding must exist. (The schema has no
    # dedicated CONVERT_FORMAT type, so the convention is that the step's reason
    # references the file_format requirement.)
    assert _steps_referencing(plan, "file_format")


def test_missing_compliance_result_raises():
    job = _design_job()
    spec = resolve_specification(job)

    with pytest.raises(ValueError):
        create_adaptation_plan(job, spec, None)


def test_adaptation_planning_does_not_generate():
    fields = set(AdaptationPlan.model_fields.keys())
    assert "generated_candidates" not in fields
    assert "generation_request" not in fields


def test_plan_references_correct_job_and_spec():
    job = _design_job()
    spec = resolve_specification(job)
    compliance = _compliance(
        spec, job, status=ResultStatus.PASSED, is_print_ready=True, findings=[]
    )

    plan = create_adaptation_plan(job, spec, compliance)

    assert plan.job_id == job.job_id
    assert plan.spec_id == spec.spec_id
