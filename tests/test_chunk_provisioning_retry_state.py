from types import SimpleNamespace

from services.project_chunk_provisioning_service import (
    ProjectChunkProvisioningError,
    ProjectChunkProvisioningService,
    ProvisioningPolicy,
)


class _Session:
    def rollback(self) -> None:
        pass

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass


def _policy() -> ProvisioningPolicy:
    return ProvisioningPolicy(
        requested_template="earth",
        fallback_template="flat",
        allow_fallback=True,
        allow_client_fallback=True,
        allow_existing_template_change=False,
        default_earth_height=0.0,
        earth_crs_id="EPSG:4979",
        cache_ttl_seconds=0.0,
        persist_failure_state=True,
    )


def test_retryable_transport_failure_stays_pending_for_reconciliation() -> None:
    project = SimpleNamespace(
        metadata_json={},
        chunk_provisioning_status="provisioning",
        chunk_world_template_requested="earth",
        chunk_provisioning_error_code=None,
        chunk_provisioning_error_message=None,
    )
    service = ProjectChunkProvisioningService(session=_Session())

    result = service._failure_result(
        project=project,
        project_public_id="prj_retryable_12345678",
        owner_user_id="auth_user_alpha",
        policy=_policy(),
        error=ProjectChunkProvisioningError(
            "chunk_service_dns_failed",
            "temporary DNS failure",
            status_code=503,
            retryable=True,
        ),
        request_id="req_retryable",
        started_at="2026-08-14T12:00:00Z",
        commit=False,
    )

    assert result.status == "pending"
    assert result.retryable is True
    assert project.chunk_provisioning_status == "pending"
    assert project.chunk_provisioning_error_code == "chunk_service_dns_failed"


def test_non_retryable_failure_remains_failed() -> None:
    project = SimpleNamespace(
        metadata_json={},
        chunk_provisioning_status="provisioning",
        chunk_world_template_requested="earth",
        chunk_provisioning_error_code=None,
        chunk_provisioning_error_message=None,
    )
    service = ProjectChunkProvisioningService(session=_Session())

    result = service._failure_result(
        project=project,
        project_public_id="prj_permanent_12345678",
        owner_user_id="auth_user_alpha",
        policy=_policy(),
        error=ProjectChunkProvisioningError(
            "invalid_earth_reference",
            "invalid reference",
            status_code=422,
            retryable=False,
        ),
        request_id="req_permanent",
        started_at="2026-08-14T12:00:00Z",
        commit=False,
    )

    assert result.status == "failed"
    assert result.retryable is False
    assert project.chunk_provisioning_status == "failed"
