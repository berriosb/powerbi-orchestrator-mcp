# Spec: Engine Error Contracts (Capa 1-2 cross-cutting)

> Contratos canónicos de error, timeout y exit code para todos los engines
> subprocess (Capas 1-2). Sin esto, cada adapter inventa su propia jerarquía
> de errores y el `selector.py` no puede degradar con gracia.

**Status:** v0.1 (spec)
**Prioridad:** P0 — bloqueante para Semana 2 (engine adapters)
**Responsable:** codehak
**Depende de:** [`01-orchestrator.md`](./01-orchestrator.md) §2.5 (rollback engine)
**Habilita:** todas las herramientas que tocan modelo o reporte
**Spec relacionado:** [`docs/architecture.md`](../docs/architecture.md) §2.2 + §2.3

---

## Cambios respecto a v0.0

N/A (spec inicial).

---

## 1. Objetivo

Definir un **contrato uniforme** que todo engine subprocess debe respetar:

- **Jerarquía de errores** tipada en Python (con `code`, `remediation_hint`, `retryable`).
- **Política de timeout** consistente con defaults por engine.
- **Formato de output**: stdout = JSON parseable, stderr = logging/debug.
- **Exit codes canónicos** mapeados 1:1 con la jerarquía.

**Métricas de éxito:**

- 100% de adapters exponen `EngineError` con `code` válido (verificable con test de contrato).
- Selector dinámico degrada correctamente cuando un engine devuelve `EngineTimeoutError` vs `EngineNotFoundError`.
- Logs de debugging no contienen stack traces de subprocess (stderr se trunca y se loguea solo en `debug`).

---

## 2. Jerarquía de errores

```python
# src/engines/errors.py
from typing import Literal

class EngineError(Exception):
    """Base para todos los errores de un engine subprocess.

    Attributes:
        engine: Nombre del engine (ej: 'powerbi-modeling-mcp').
        code: Código canónico string (ver tabla §4).
        remediation_hint: Mensaje accionable para el usuario.
        retryable: Si el orquestador debe reintentar automáticamente.
        timeout_s: Si el error fue por timeout, cuánto se le dio.
    """

    engine: str
    code: str
    remediation_hint: str
    retryable: bool = False
    timeout_s: int | None = None

    def __init__(
        self,
        message: str,
        *,
        engine: str,
        code: str,
        remediation_hint: str,
        retryable: bool = False,
        timeout_s: int | None = None,
    ) -> None:
        super().__init__(message)
        self.engine = engine
        self.code = code
        self.remediation_hint = remediation_hint
        self.retryable = retryable
        self.timeout_s = timeout_s


class EngineNotFoundError(EngineError):
    """El binario o paquete no está instalado o no está en PATH."""


class EngineVersionMismatchError(EngineError):
    """La versión instalada no está en el rango soportado por el adapter."""


class EngineTimeoutError(EngineError):
    """El subprocess excedió el timeout. retryable=True por default."""


class EngineCrashedError(EngineError):
    """El subprocess murió con signal (segfault, OOM killed) o exit code inesperado."""


class EngineOutputParseError(EngineError):
    """stdout no es JSON parseable o no cumple el schema esperado."""


class EngineAuthError(EngineError):
    """Fallo de autenticación (token expirado, scopes insuficientes)."""


class EngineValidationError(EngineError):
    """El engine rechazó la operación por validación (no retry)."""


class EngineContractError(EngineError):
    """El engine no respeta el contrato esperado (schema drift). retryable=False."""
```

**Regla de herencia:** todas las subclases extienden `EngineError` con
`engine=<nombre>` pre-set. Ejemplo:

```python
raise EngineTimeoutError(
    "te bpa excedió el timeout",
    engine="te",
    code="engine_timeout",
    remediation_hint="Reintentá con un modelo más chico o aumentá PBI_ENGINE_TIMEOUT_TE_S",
    retryable=True,
    timeout_s=60,
)
```

---

## 3. Timeouts

```python
# src/engines/timeouts.py
from dataclasses import dataclass

@dataclass(frozen=True)
class EngineTimeout:
    default_s: int
    max_s: int  # cap superior que el usuario no puede exceder
    long_running: bool = False  # True para operaciones que pueden durar minutos


DEFAULT_TIMEOUTS: dict[str, EngineTimeout] = {
    "powerbi-modeling-mcp": EngineTimeout(default_s=30, max_s=120),
    "te": EngineTimeout(default_s=60, max_s=300),
    "dscmd": EngineTimeout(default_s=90, max_s=600),
    "pbip-validator": EngineTimeout(default_s=15, max_s=60),
    "superbi-mcp": EngineTimeout(default_s=45, max_s=180),
}

# Overrides por env var (en segundos)
ENV_TIMEOUT_OVERRIDES = {
    "PBI_ENGINE_TIMEOUT_POWERBI_MODELING_MCP_S": "powerbi-modeling-mcp",
    "PBI_ENGINE_TIMEOUT_TE_S": "te",
    "PBI_ENGINE_TIMEOUT_DSCMD_S": "dscmd",
    "PBI_ENGINE_TIMEOUT_PBIP_VALIDATOR_S": "pbip-validator",
    "PBI_ENGINE_TIMEOUT_SUPERBI_S": "superbi-mcp",
}
```

**Reglas:**

1. Si el usuario no especifica timeout, se usa `default_s`.
2. El usuario puede subir el timeout por tool call hasta `max_s`; valores mayores → error `invalid_timeout` antes de invocar.
3. Si el timeout expira, se hace `subprocess.terminate()` (SIGTERM); tras 5s sin respuesta, `subprocess.kill()` (SIGKILL). El error devuelto es `EngineTimeoutError` con `timeout_s` real gastado.
4. `te bpa` y `dscmd` (especialmente trace FE/SE) tienen `long_running=True` por operación; el caller debe especificar timeout explícito ≥120s para esas operaciones o el adapter eleva `EngineTimeoutError` con `remediation_hint` sugeriendo el valor correcto.

---

## 4. Exit codes canónicos

Todo adapter traduce el exit code del subprocess a un `EngineError` o `EngineResult`.

| Exit code | Significado | EngineError a emitir | Notas |
|-----------|-------------|----------------------|-------|
| 0 | OK | (ninguno, retorna `EngineResult`) | stdout contiene JSON del resultado |
| 1 | Validation failed (input rechazado por el engine) | `EngineValidationError` con detalle en `message` | NO retryable — el input es inválido |
| 2 | Auth failed | `EngineAuthError` con `remediation_hint` apuntando a scopes/token | NO retryable — el orquestador elicita al usuario |
| 3 | Timeout interno del engine (el engine abortó su propia operación) | `EngineTimeoutError` con `timeout_s=internal` | retryable |
| 4 | Crash (signal 11, SIGSEGV, OOM killed, etc.) | `EngineCrashedError` con código de signal | NO retryable — log full stderr, abrir issue |
| 5 | Incompatible version | `EngineVersionMismatchError` con versión instalada vs requerida | NO retryable — el adapter documenta upgrade |
| 64+ | Contract violation | `EngineContractError` con `code=engine_contract_violation` | NO retryable — romper el contrato es bug del adapter |
| 127 | Command not found | `EngineNotFoundError` | NO retryable — instalación faltante |
| otros | Unknown | `EngineCrashedError` con `code=engine_unknown_exit_<N>` | NO retryable |

**Implementación helper:**

```python
# src/engines/exit_codes.py
def map_exit_code_to_error(
    engine: str, exit_code: int, stderr: str, timeout_s: int | None
) -> EngineError | None:
    """Mapea exit code a EngineError. None si exit_code == 0."""
    ...
```

---

## 5. Formato de output

**stdout:** todo engine subprocess DEBE emitir un único objeto JSON al
final (un objeto, no NDJSON en MVP). El adapter lo parsea con
`json.loads(stdout.strip())` y lo valida contra el Pydantic model esperado
de la operación.

**stderr:** texto libre con fines de debugging. El adapter lo captura,
trunca a 4KB, y lo guarda en:

- `audit_log.payload_json.stderr_excerpt` (primeros 4KB).
- Logs de aplicación (`structlog`) solo si log level = DEBUG.

Nunca se loguea stderr con INFO o superior en producción (puede contener
PII del modelo).

**Schema de output mínimo:**

```python
class EngineResult(BaseModel):
    """Todos los adapters retornan esto (o subclase)."""
    engine: str
    engine_version: str
    operation: str
    success: bool
    data: dict[str, Any] | None = None       # output específico de la operación
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int
```

**Validación:** si `stdout` no es JSON válido → `EngineOutputParseError`.
Si el JSON parsea pero no cumple `EngineResult` → `EngineOutputParseError`
con detalle del campo faltante.

---

## 6. Logging y debugging

**Lo que el orquestador loguea automáticamente:**

- Cada invocación de subprocess: `engine=<name> args=<redacted> exit_code=<N> duration_ms=<N>`
- Cada `EngineError` con `code`, `message`, NO con stack trace completo (eso solo en DEBUG).

**Lo que se redacta antes de loguear:**

- Tokens, bearer strings, connection strings (regex patterns).
- Paths absolutos que contienen `Users/<name>` en Windows o `/home/<name>` en Linux → reemplazar por `~`.
- `effective_identity` emails en DAX queries con RLS → hashear.

---

## 7. Acceptance criteria

- [ ] Todos los adapters implementan `map_exit_code_to_error()` consistentemente.
- [ ] Test de contrato verifica que cada subclase de `EngineError` se puede instanciar con los campos requeridos.
- [ ] Test e2e: un mock subprocess que devuelve exit 3 → adapter eleva `EngineTimeoutError` con `code=engine_timeout`.
- [ ] Test e2e: un mock subprocess que cuelga >5s → adapter eleva `EngineTimeoutError` con `timeout_s` real gastado.
- [ ] `selector.py` distingue correctamente entre `EngineNotFoundError` (degrada) y `EngineValidationError` (no degrada, propaga).
- [ ] Defaults de timeout documentados en `docs/engines-setup.md`.
- [ ] 0 secretos en logs de aplicación (test automatizado que parsea stdout/stderr capturado).

---

## 8. Out of scope (MVP)

- ❌ Streaming de output (NDJSON) — solo single JSON object en MVP.
- ❌ Métricas Prometheus detalladas por engine — solo `duration_ms` en MVP.
- ❌ Circuit breaker por engine — el circuit breaker vive en Capa 3 (`fabric_client`), no aplica a subprocess engines locales.

---

## 9. Riesgos

| Riesgo | Mitigación |
|--------|-----------|
| Engine nuevo no respeta los exit codes documentados | Adapter tests con mocks; documentar el mapeo específico del engine en su spec. |
| Timeout demasiado corto para modelos grandes | Defaults por env var; advertencia si el modelo tiene >500 measures (sugerir subir). |
| Stderr con PII llega al audit log | Redacción obligatoria antes de persistir; test automatizado. |
| `EngineCrashedError` muy genérico | El `remediation_hint` debe incluir los últimos 200 chars de stderr + comando exacto + versión del engine. |

---

## 10. Specs relacionados

- [`01-orchestrator.md`](./01-orchestrator.md) §2.5 — rollback engine que consume estos errores
- [`05-engines-adapters.md`](./05-engines-adapters.md) — adapters concretos que emiten estos errores
- [`02-cloud-fabric.md`](./02-cloud-fabric.md) — Capa 3 NO usa estos errores (REST, no subprocess); usa `CloudAPIError` propio
