import inspect

import pytest

from app.services import print_interfaces as pi


ALL_INTERFACES = [
    "SubmissionIntakeService",
    "NormalizationService",
    "SpecificationResolutionService",
    "TechnicalComplianceService",
    "AdaptationPlanningService",
    "PromptConstructionService",
    "AIGenerationService",
    "OutputValidationService",
    "ApprovalWorkflowService",
    "ProductionPackagingService",
    "AuditObservationService",
    "StorageAssetService",
    "ConfigurationService",
]

EXPECTED_METHODS = {
    "SubmissionIntakeService": ["intake_submission"],
    "NormalizationService": ["normalize_submission"],
    "SpecificationResolutionService": ["resolve_specification"],
    "TechnicalComplianceService": ["evaluate_compliance"],
    "AdaptationPlanningService": ["create_adaptation_plan"],
    "PromptConstructionService": ["build_generation_request"],
    "AIGenerationService": ["generate_candidates"],
    "OutputValidationService": ["validate_candidates"],
    "ApprovalWorkflowService": ["create_approval_package", "record_approval_decision"],
    "ProductionPackagingService": ["create_production_package"],
    "AuditObservationService": ["record_event", "record_transition"],
    "StorageAssetService": ["store_asset", "resolve_asset"],
    "ConfigurationService": ["get_config_version", "get_specification_rules"],
}

EXPECTED_PARAMS = {
    ("NormalizationService", "normalize_submission"): ["self", "raw_submission"],
    ("SpecificationResolutionService", "resolve_specification"): ["self", "design_job"],
    (
        "TechnicalComplianceService",
        "evaluate_compliance",
    ): ["self", "design_job", "specification", "image_properties"],
    ("AIGenerationService", "generate_candidates"): ["self", "generation_request"],
    (
        "ApprovalWorkflowService",
        "create_approval_package",
    ): ["self", "specification", "design_job", "validation_result"],
    (
        "ApprovalWorkflowService",
        "record_approval_decision",
    ): ["self", "approval_package", "status", "approver"],
    (
        "ProductionPackagingService",
        "create_production_package",
    ): ["self", "approval_decision", "approved_candidate", "specification"],
}


@pytest.mark.parametrize("name", ALL_INTERFACES)
def test_all_interfaces_import(name):
    assert hasattr(pi, name)
    assert inspect.isclass(getattr(pi, name))


@pytest.mark.parametrize("name", ALL_INTERFACES)
def test_interface_exposes_expected_methods(name):
    interface = getattr(pi, name)
    for method_name in EXPECTED_METHODS[name]:
        assert hasattr(interface, method_name)
        assert callable(getattr(interface, method_name))


@pytest.mark.parametrize("name,method,expected_params", [
    (n, m, p) for (n, m), p in EXPECTED_PARAMS.items()
])
def test_method_signature_param_names(name, method, expected_params):
    interface = getattr(pi, name)
    sig = inspect.signature(getattr(interface, method))
    assert list(sig.parameters.keys()) == expected_params


def test_normalization_service_isinstance_with_stub():
    class StubNormalization:
        def normalize_submission(self, raw_submission):
            ...

    assert isinstance(StubNormalization(), pi.NormalizationService)


def test_configuration_service_isinstance_with_stub():
    class StubConfiguration:
        def get_config_version(self):
            ...

        def get_specification_rules(self, product_type):
            ...

    assert isinstance(StubConfiguration(), pi.ConfigurationService)


def test_incomplete_stub_is_not_instance():
    class MissingMethod:
        def get_config_version(self):
            ...

    # Missing get_specification_rules -> should not satisfy the protocol.
    assert not isinstance(MissingMethod(), pi.ConfigurationService)


def test_empty_stub_is_not_normalization_service():
    class Empty:
        pass

    assert not isinstance(Empty(), pi.NormalizationService)
