# Feature Specs — powerbi-orchestrator-mcp

> Specs modulares por capa y por tool. Cada spec es un entregable implementable.
>
> **Última actualización:** 2026-09-24 (specs v0.3, post-PyPI-publication)

> Las casillas de esta lista indican que existe la spec, no que todos sus
> criterios estén verificados. Para estado de implementación consultar
> [`docs/MVP-STATUS.md`](../docs/MVP-STATUS.md).

---

## Cómo leer este repo de specs

- **[`SPEC.md`](../SPEC.md)** — visión + arquitectura 6 capas + stack + MVP.
- **[`docs/architecture.md`](../docs/architecture.md)** — arquitectura detallada con diagramas.
- **Specs por capa (`0X-*.md`)** — cómo se implementa cada capa del servidor.
- **Specs por tool (`tools/*.md`)** — herramientas individuales de alto nivel.
- **Specs por workflow (`workflows/*.md`)** — flujos end-to-end compuestos.
- **Specs de release / CI / QA (`release/`, `ci/`, `qa/`)** — proceso de publicación, política de PyPI, estrategia de tests E2E.
- **[`docs/MVP-STATUS.md`](../docs/MVP-STATUS.md)** — qué está implementado vs qué es spec.
- **[`docs/IMPLEMENTATION-PLAN-v1.0.md`](../docs/IMPLEMENTATION-PLAN-v1.0.md)** — roadmap.

---

## Specs por capa

- [x] `01-orchestrator.md` — Capa 6: planner, connect_target, apply_plan, rollback engine, **identifiers (§2.7)**, **crash recovery (§2.8)**, **elicitation outcomes + flags globales (§2.9)**, **audit key rotation (§2.10)**, **plan YAML versioning (§5.1)**.
- [x] `02-cloud-fabric.md` — Capa 3: REST Fabric, auth, deploy, refresh, RLS, git integration, **cloud audit log (§5)**, **concurrency limits + rate limiting (§6)**.
- [x] `03-validation.md` — Capa 4: BPA, DAX linter, regression runner, accessibility, pre-deploy gate.
- [x] `04-viz-ux.md` — Capa 5: visual registry, suggester, layout, theme, storytelling.
- [x] `05-engines-adapters.md` — Cómo se delega a `powerbi-modeling-mcp`, `superbi-mcp`, `te`, `dscmd`, `pbip-validator`, **integración `pbip-validator` (§10)**, **matriz `connect_target` (§11)**.
- [x] [`architecture/07-http-transport.md`](./architecture/07-http-transport.md) — **Capa 0 (transport)**: Streamable HTTP + Entra ID OAuth, scopes, audience validation, threat model, migration strategy (stdio default, HTTP opt-in). Capa nueva — habilita deployments remotos (creado 2026-09-24, post-v1.9.1).

## Specs cross-cutting

- [x] `06-engine-error-contracts.md` — Jerarquía de errores, timeouts y exit codes canónicos para todos los subprocess engines (creado 2026-08-26, pre-Semana 2).
- [x] [`../tests/fixtures/README.md`](../tests/fixtures/README.md) — Especificación del fixture PBIP load-bearing (4 tablas, ~15 medidas, 4 visuales, RLS) usado por todos los tests e2e (creado 2026-08-26, pre-Semana 4).
- [x] [`release/supersede-policy.md`](./release/supersede-policy.md) — Política de supersede / yank en PyPI: cuándo un release nuevo invalida al anterior, decision tree para `pip install` (creado 2026-09-24, post-v1.9.1).
- [x] [`ci/publish-workflow.md`](./ci/publish-workflow.md) — Diseño del workflow `publish.yml` de GitHub Actions: triggers (tag push + manual), gate de TestPyPI, environments protegidos, OIDC vs API token (creado 2026-09-24, post-v1.9.1).
- [x] [`qa/e2e-testing-strategy.md`](./qa/e2e-testing-strategy.md) — Estrategia para tests con engines reales (`te`, `dscmd`, `pbip-validator`): binaries pinned por SHA256, nightly vs on-PR, escenarios por tool, CI matrix (creado 2026-09-24, post-v1.9.1).

## Specs por tool (MVP ambicioso)

- [x] `tools/safe-rename.md` — Tool estrella: rename cross-engine (model + DAX + M + report bindings) con rollback.
- [x] `tools/audit-model-and-report.md` — Auditoría integral: BPA + WCAG + lint + star-schema + naming.
- [x] `tools/deploy-to-workspace.md` — Publish PBIP a Fabric workspace con refresh + RLS + labels.
- [x] `tools/generate-data-dictionary.md` — Data dictionary Markdown/HTML con diagrama Mermaid + coverage score (creado 2026-08-26).

### Tools MVP v1 documentadas en specs por capa (no en `specs/tools/`)

Estas tools están completamente especificadas dentro del spec de la capa
correspondiente, en su sección dedicada. No se duplican como archivos
separados en `specs/tools/` para evitar drift entre copias:

- [x] `connect_target` → [`01-orchestrator.md` §3.1](./01-orchestrator.md)
- [x] `plan_change` → [`01-orchestrator.md` §3.2](./01-orchestrator.md)
- [x] `apply_plan` → [`01-orchestrator.md` §3.3](./01-orchestrator.md)
- [x] `run_refresh` → [`02-cloud-fabric.md` §3](./02-cloud-fabric.md)
- [x] `run_dax_regression` → [`03-validation.md` §5](./03-validation.md) (input/output schema YAML completo).
- [x] `diff_models` → [`03-validation.md` §2.4](./03-validation.md) (Pydantic models + reglas de breaking).
- [x] `pre_deploy_check` → [`03-validation.md` §4](./03-validation.md) (input/output schema + profiles).
- [x] `apply_theme_and_accessibility_rules` → [`04-viz-ux.md` §3](./04-viz-ux.md) (theme.json + WCAG rules).

**Decisión arquitectónica (post-audit 2026-08-26):** un tool MVP v1 puede
tener su spec dedicado en `specs/tools/` (si su lógica es ortogonal a una
capa única) o vivir dentro del spec de la capa que lo implementa (si es
parte del dominio de esa capa). Esto evita proliferación de archivos y
duplicación de schemas.

## Specs por workflow

- [x] `workflows/01-from-csv-to-published-report.md` — De cero a reporte publicado con RLS en un prompt.
- [x] `workflows/02-refactor-to-calc-groups.md` — Refactor medidas → calc group con reconciliación de totales. **No MVP** (depende de `refactor_to_calculation_groups` v2).

## Specs pendientes (post-MVP)

### v1.1 — Semana 5

- [ ] `tools/add-measure-with-validation.md` → v1.1 (spec dedicado a crear)
- [ ] `tools/create-report-from-dataset.md` → v1.1 (spec dedicado a crear)
- [ ] `tools/edit-report-visual.md` → v1.1 (spec dedicado a crear)

### v2 — Semanas 6-8

- [x] `tools/refactor-to-calculation-groups.md` (con reconciliation total) — outline v0.1
- [x] `tools/promote-in-pipeline.md` (dev→test→prod gates) — outline v0.1
- [x] `tools/design-report-page-from-requirements.md` (viz/UX completa) — outline v0.1
- [x] `tools/select-visuals-for-kpis.md` — outline v0.1
- [x] `tools/audit-report-ux-and-storytelling.md` — outline v0.1
- [x] `tools/setup-rls-and-roles.md` — outline v0.1
- [x] `tools/create-semantic-model-from-schema.md` — outline v0.1
- [x] `tools/screenshot-report-pages.md` — outline v0.1

### v3 — Semanas 9-12

- [x] `tools/sync-git-to-workspace.md` — outline v0.1
- [ ] `tools/set-sensitivity-labels.md` (pendiente outline; governance)

## Cambios v0.3 (audit 2026-09-24)

Cierre del backlog "post-PyPI-publication": 4 specs nuevos que documentan
decisiones pendientes desde la publicación de v1.9.0. Cero código nuevo;
solo docs. Estos specs son los entregables que preceden a cualquier
implementación de los features correspondientes.

- ✅ Nuevo directorio `specs/release/` con `supersede-policy.md` — política de yank vs patch vs leave; documenta el caso v1.9.0 vs v1.9.1 (no yank) y los triggers para futuras decisiones (security CVE, metadata drift, etc.).
- ✅ Nuevo directorio `specs/ci/` con `publish-workflow.md` — diseño del workflow `publish.yml` que reemplaza el flujo manual de `twine upload`. Cubre tag-push trigger, TestPyPI gate, environments protegidos, OIDC vs API token. Documenta también el gap actual: CONTRIBUTING.md describe un workflow que no existe todavía.
- ✅ Nuevo directorio `specs/qa/` con `e2e-testing-strategy.md` — estrategia para tests con engines reales (`te`, `dscmd`, `pbip-validator`), binaries pinned por SHA256, nightly vs on-PR decision matrix, primer test scenario concreto (`test_te_modeling.py::test_add_measure_with_te_validates_dax`).
- ✅ Nuevo directorio `specs/architecture/` con `07-http-transport.md` — **Capa 0** del orquestador: Streamable HTTP transport + Entra ID OAuth (decisión: PyJWT con JWKS, no MSAL). Incluye threat model, scopes RBAC (`Tools.Read`/`Tools.Write`/`Tools.Admin`), migration strategy (stdio default, HTTP opt-in vía `--transport http`).
- ✅ Header del README: timestamp de "Última actualización" bumped a 2026-09-24.
- ✅ Sección "Specs por capa" extendida con la nueva Capa 0.
- ✅ Sección "Specs cross-cutting" extendida con las 3 specs de proceso (release/CI/QA).
- ✅ Sección "Cómo leer este repo de specs" agrega línea sobre specs de release/CI/QA.

Drift fix incluido (sin spec):

- ✅ README.md: conteo de tools consolidado a 27 (5 lugares: Quickstart, sección "Arquitectura", diagrama ASCII, sección "Probar", "Estado actual"). Incluye `examples/README.md`.

Spec pendientes para implementación posterior (no specs nuevos):

- 🔲 Workflow `.github/workflows/publish.yml` (de `publish-workflow.md`).
- 🔲 `.github/workflows/e2e-nightly.yml` + `tests/e2e/` (de `e2e-testing-strategy.md`).
- 🔲 `src/powerbi_orchestrator_mcp/orchestrator/server.py --transport http` + `pyjwt[crypto]` dep (de `07-http-transport.md`).
- 🔲 Posible bump a v1.9.2 / v1.10.0 cuando alguno de los 3 anteriores se implemente.

## Cambios v0.2 (audit 2026-08-26)

- ✅ Conteos corregidos: 26 tools catálogo (era 28), 12 MVP (sin cambios), 14 no-MVP (10 v2 + 4 v3).
- ✅ `tools/generate-data-dictionary.md` creado (1 página con schema completo).
- ✅ Workflow 01 §9: tabla de 3 columnas (MVP / MVP+v1.1 / v2) para distinguir alcances.
- ✅ Links rotos a specs inexistentes eliminados (`audit-model-and-report.md`, `04-viz-ux.md`).
- ✅ MVP-STATUS.md actualizado con issues abiertos, decisión de plan renegociado (5 semanas), y tabla de ubicación real de specs.
- ✅ Decisión arquitectónica adoptada: specs pueden vivir en `specs/tools/` (dedicado) o en spec por capa (inline).
- ✅ Plan renegociado a 5 semanas: Semana 5 v1.1 con `add_measure_with_validation`, `create_report_from_dataset`, `edit_report_visual`.
- ✅ `06-engine-error-contracts.md` creado (errores canónicos para subprocess engines).
- ✅ `01-orchestrator.md` §2.7 (Identifiers) y §2.8 (Crash recovery & plan state machine) añadidos.
- ✅ `05-engines-adapters.md` §10 (integración `pbip-validator`) y §11 (matriz `connect_target` por tipo de target) añadidos.
- ✅ SPEC.md §6.3: nota explícita que MVP done = MVP + v1.1 (opción C, decisión 2026-08-26).
- ✅ README.md: conteo de tools corregido (28 → 26).
- ✅ Tier B cerrado: `01-orchestrator.md` §2.9 (elicitation outcomes + `--readonly`/`--allow-prod`/`--allow-refresh` flags) y §2.10 (HMAC key rotation CLI manual).
- ✅ Tier B cerrado: `02-cloud-fabric.md` §5 (Cloud audit log con redacción obligatoria) y §6 (concurrency limits, token bucket, circuit breaker).
- ✅ Tier C cerrado: `01-orchestrator.md` §5.1 (Plan YAML schema versioning) y `tests/fixtures/README.md` (especificación del fixture PBIP con cobertura amplia: 10 anti-patterns DAX + RLS 3 roles + 4 visuales + WCAG baseline + 1 error DAX intencional).
- ✅ Fase 4 cerrada: 9 outlines v2/v3 (8 v2 + 1 v3) en `specs/tools/`. Formato uniforme de 1 página.

## Cambios v0.1 (sync 2026-08-21)

- ✅ Creada la estructura modular de specs (5 capas + 3 tools + 2 workflows = 10 specs).
- ✅ Decisión arquitectónica: **delegar** capas 1-2 a engines existentes, **implementar propio** capas 3-6.
- ✅ MVP ambicioso: 12 tools, 4 semanas de un dev senior.
- ✅ Stack: Python 3.11 + `mcp[cli]` + `azure-identity` + `httpx`.
