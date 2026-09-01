"""Cloud layer (Capa 3): Microsoft Fabric / Power BI Service operations.

Implements ``specs/02-cloud-fabric.md``:
- ``auth.py`` — Azure Identity async wrapper (interactive / SPN / managed identity)
- ``fabric_client.py`` — REST client with retry + circuit breaker + token bucket
- ``refresh.py`` — RefreshOrchestrator + RefreshDoctor
- ``audit_cloud.py`` — CloudAuditLog with mandatory PII redaction
"""

from powerbi_orchestrator_mcp.cloud.audit_cloud import (
    EMAIL_PATTERN,
    REDACTION_PATTERNS,
    CloudAuditLog,
    redact_payload,
)
from powerbi_orchestrator_mcp.cloud.auth import (
    READ_SCOPES,
    WRITE_SCOPES,
    AuthConfig,
    AuthModeError,
    FabricCredential,
)
from powerbi_orchestrator_mcp.cloud.fabric_client import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    FabricAPIError,
    FabricClient,
    TokenBucket,
)
from powerbi_orchestrator_mcp.cloud.refresh import (
    RefreshDoctor,
    RefreshOrchestrator,
    RefreshResult,
)

__all__ = [
    "AuthConfig",
    "AuthModeError",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "CloudAuditLog",
    "EMAIL_PATTERN",
    "FabricAPIError",
    "FabricClient",
    "FabricCredential",
    "READ_SCOPES",
    "REDACTION_PATTERNS",
    "RefreshDoctor",
    "RefreshOrchestrator",
    "RefreshResult",
    "TokenBucket",
    "WRITE_SCOPES",
    "redact_payload",
]
