# powerbi-orchestrator-mcp

> **Un servidor MCP orquestrador** que unifica modelado semántico, autoría
> de reportes, nube Fabric, validación y visualización/UX para Power BI /
> Fabric en **31 herramientas de alto nivel** (no 500 primitivas).
>
> El orquestrador **delega** a motores especializados (subprocess) y
> presenta al LLM una superficie coherente y de alto nivel.

[![Tests](https://img.shields.io/badge/tests-1568%20passing-brightgreen)](./tests/)
[![Coverage](https://img.shields.io/badge/coverage-89%25-brightgreen)](./tests/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](./pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)
[![MCP](https://img.shields.io/badge/MCP-stdio-purple)](https://modelcontextprotocol.io)

**Status:** v1.22.0 — Beta. 31 tools, 89% cobertura, enterprise hardening
(per-tool scopes, read- and write-path containment, plan ownership, full
audit trail).
Every state-changing tool is safe by default: `dry_run=True` on
`deploy_to_workspace` and `run_refresh`, per-step confirmation on
`apply_plan`, and `Tools.Write` required for mutating tools over HTTP.
Every tool also **reports what it actually did**: a check that could not
run says so instead of returning a green result, a rollback that only
partly succeeded is reported as partial, and a step says which engine
served it.
See [release notes](./RELEASE-NOTES-v1.22.0.md) · [changelog](./CHANGELOG.md).

---

## Quickstart (60 segundos)

```bash
# 1. Install from PyPI.
pip install powerbi-orchestrator-mcp

# 2. Configure your MCP client (Claude Desktop shown).
#    Edit claude_desktop_config.json:
{
  "mcpServers": {
    "powerbi-orchestrator-mcp": {
      "command": "powerbi-orchestrator-mcp",
      "args": ["--start"],
      "env": {"PBI_AUTH_MODE": "interactive"}
    }
}
```

```bash
# 3. Verify the orchestrator itself works.
powerbi-orchestrator-mcp --start  # runs over stdio
# Or in another terminal, run the smoke test:
python scripts/verify_mcp_server.py
```

That's it — your LLM now sees 31 tools for Power BI / Fabric. See the
[`examples/`](./examples/) directory for 3 reproducible workflows.

> **Antes de usarlo con datos de clientes**, la suite de tests no cubre nada
> contra una API real. Sigue
> [`docs/pilot-validation.md`](./docs/pilot-validation.md) y ejecuta el
> preflight de solo lectura:
>
> ```bash
> python scripts/pilot_check.py --workspace-id <ws-id> --dataset-id <ds-id>
> ```

---

## Defaults de seguridad (léelo antes de tocar producción)

Todas las tools que modifican estado son **safe-by-default**. Nada escribe
en un workspace real ni en disco sin una acción explícita.

| Tool | Default | Cómo se ejecuta de verdad |
|---|---|---|
| `deploy_to_workspace` | `dry_run=True` | `dry_run=False` |
| `run_refresh` | `dry_run=True` | `dry_run=False` |
| `apply_plan` | `dry_run=True` | `dry_run=False` (+ `confirm_each_step`) |
| `promote_in_pipeline` | `dry_run=True` | `dry_run=False` |
| `run_dax_regression` | no ejecuta sin dataset | pasar `workspace_id` + `dataset_id` |

Además:

- **Sin secretos en el contexto del LLM.** Ninguna tool acepta un
  `client_secret`. Las credenciales se leen del entorno
  (`PBI_CLIENT_ID` / `PBI_TENANT_ID` / `PBI_CLIENT_SECRET`, o los
  equivalentes `AZURE_*`) o de una workload identity.
- **Las escrituras están contenidas.** Las tools que escriben en disco
  rechazan rutas fuera de `PBI_WORKSPACE_ROOT`, que por defecto es el
  directorio de trabajo del proceso.
- **Las lecturas también.** Leer es cómo el contenido llega al contexto
  del modelo, así que el path de lectura sigue la misma regla que el de
  escritura. La precedencia es deliberada: un `PBI_WORKSPACE_ROOT`
  configurado **es** la frontera y nada lo ensancha;
  `PBI_ALLOW_OUTSIDE_WORKSPACE_READ=1` la quita solo cuando no hay
  ninguna configurada (en ese caso la frontera es el directorio de
  trabajo del proceso). Los directorios del sistema y de credenciales
  (`/etc`, `/run`, `~/.ssh`, `~/.config`…) se rechazan siempre.
- **Auditoría de todo lo que muta.** Las 20 tools con efecto sobre el
  workspace dejan registro (`success` / `failed` / `dry_run` / `partial` /
  `rolled_back`) en un log encadenado con HMAC. Los valores guardados bajo
  una clave que nombra un secreto se redactan enteros.
- **DAX acotado.** `execute_dax_query` devuelve como máximo 1000 filas por
  defecto (techo 10 000) y marca el truncamiento.
- **Scopes por tool** sobre transporte HTTP: cada una de las 31 tools
  declara el scope que necesita. Las que **modifican el workspace o la
  nube** requieren `Tools.Write`; liberar entre etapas de un pipeline y
  aplicar sensitivity labels requieren `Tools.Admin`; `Tools.Admin`
  concede todos los inferiores. Las que solo **producen ficheros**
  dentro del workspace (el diccionario de datos, las capturas) siguen
  siendo de lectura, pero su ruta de salida se valida como escritura
  y queda dentro del workspace. Cada sesión HTTP queda ligada a la
  credencial que la abrió: otra credencial recibe 404.
- **Los planes tienen dueño.** Cada plan registra la identidad que lo creó y
  solo su dueño puede leerlo, listarlo o borrarlo; conocer el `plan_id`
  no alcanza.
- **Plugins desactivados por defecto.** Cargar un plugin ejecuta Python
  arbitrario, así que requiere `PBI_ENABLE_PLUGINS=1` y un directorio
  escribible solo por el propietario.

### Lo que una tool te dice es lo que ocurrió

Este proyecto no reporta éxito por trabajo que no hizo:

- Una validación que no pudo ejecutarse (por ejemplo, BPA sin Tabular
  Editor) devuelve un score **parcial** con la lista de checks omitidos,
  no un 100.
- Un paso de plan sin ejecutor falla con el nombre de la acción, en vez de
  devolverse como aplicado.
- `run_dax_regression` falla si no encuentra el baseline: sin baseline no
  hay nada contra qué comparar.
- `apply_plan` distingue `rolled_back` de `rollback_partial` y de
  `rollback_incomplete`, y cada entrada del rollback lleva su estado real.

Detalle completo del modelo de amenazas en [`SECURITY.md`](./SECURITY.md),
incluidos los controles que **no** existen.

---

## Arquitectura de 3 capas (importante)

El proyecto **NO** es un wrapper sobre los MCP servers existentes. Es un
servidor MCP propio que **consume** otros MCP servers como subprocess.
Esto es lo que permite presentar al LLM 31 tools coherentes en lugar
de 500 primitivas dispersas.

```
┌─────────────────────────────────────────────────────────────────┐
│ Capa 1: MCP Client (Claude Desktop, VS Code, Copilot, Cursor)    │
│         Habla JSON-RPC sobre stdio con el orquestrador.          │
│         El LLM ve 31 tools de alto nivel.                        │
└────────────────────────────┬────────────────────────────────────┘
                             │ stdio + JSON-RPC
┌────────────────────────────▼────────────────────────────────────┐
│ Capa 2: powerbi-orchestrator-mcp (ESTE PAQUETE — Python)        │
│         Distribuido via PyPI: pip install powerbi-orchestrator-mcp│
│         Console script: powerbi-orchestrator-mcp                 │
└────────────────────────────┬────────────────────────────────────┘
                             │ subprocess + JSON-RPC sobre stdio
┌────────────────────────────▼────────────────────────────────────┐
│ Capa 3: Engines individuales (heterogéneos)                       │
│         powerbi-modeling-mcp → npm: npx @microsoft/...          │
│         te (Tabular Editor)    → .NET binary                     │
│         superbi-mcp             → npm: npx superbi-mcp            │
│         python_report           → built-in (parte del orquestrador)│
└─────────────────────────────────────────────────────────────────┘
```

La capa 3 son los motores a los que el orquestrador **despacha**, y son
todos los que el `health` check reporta: si aparece ahí, alguna tool lo
usa.

**Por qué npm NO es necesario para instalar el orquestrador** (sí para
correr operaciones reales): npm es una dependencia RUNTIME de los
engines, no del orquestrador. El paquete `powerbi-orchestrator-mcp` se
publica solo en PyPI.

---

## Qué es

Un servidor [Model Context Protocol](https://modelcontextprotocol.io)
(stdio) que expone **26 herramientas de alto nivel** para que un agente
IA pueda trabajar end-to-end con Power BI:

- Diseñar y validar modelos semánticos (TMDL/TOM).
- Crear, editar y auditar reportes (`.pbix`, PBIP/PBIR).
- Operar en la nube (Fabric / Power BI Service): workspaces, datasets, refresh,
  deployment pipelines, RLS, Git integration.
- Auditar calidad (BPA, lint DAX, accesibilidad WCAG, star-schema).
- Diseñar visualizaciones con razonamiento de UX/storytelling.

## Qué problema resuelve

Los MCPs existentes cubren **partes**:

- `powerbi-modeling-mcp` (oficial MS): solo modelo semántico, no toca reportes.
- `superbi-mcp` (cyphonica, 490 tools): local, Windows-only, FSL.
- `powerbi-mcp` (sulaiman013, 82 tools): cloud paths mock-tested, no live.
- `fabric-rti-mcp`, `Fabric Core MCP`: solo nube, no autoría local.

Nadie entrega **orquestación cross-engine + nube maduro + UX verificable**.
`powerbi-orchestrator-mcp` sí.

## Quick links

- [`examples/`](./examples/) — 3 workflows reproducibles (safe rename, deploy, audit loop).
- [`CHANGELOG.md`](./CHANGELOG.md) — condensed changelog.
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — dev setup, testing, release process.
- [`SECURITY.md`](./SECURITY.md) — how to report vulnerabilities.
- [`docs/troubleshooting.md`](./docs/troubleshooting.md) — common errors with remediation.
- [`SPEC.md`](./SPEC.md) — visión, arquitectura 6 capas, MVP ambicioso.
- [`RELEASE-NOTES-v1.20.0.md`](./RELEASE-NOTES-v1.20.0.md) — última release con decisiones.
- [`docs/architecture.md`](./docs/architecture.md) — arquitectura detallada.
- [`docs/engines-setup.md`](./docs/engines-setup.md) — instalar engines opcionales.
- [`docs/connect-target.md`](./docs/connect-target.md) — uso del entry-point tool.
- [`specs/`](./specs/README.md) — specs modulares por capa + por tool.
- [`docs/MVP-STATUS.md`](./docs/MVP-STATUS.md) — estado de implementación.

## Instalación

### 1. Instalar el orquestrador (Python)

```bash
pip install powerbi-orchestrator-mcp
```

El paquete está publicado en PyPI: https://pypi.org/project/powerbi-orchestrator-mcp/

Para desarrollo local (con tests y dev dependencies):

```bash
git clone https://github.com/berriosb/powerbi-orchestrator-mcp.git
cd powerbi-orchestrator-mcp
pip install -e ".[dev]"
```

El comando `powerbi-orchestrator-mcp` queda disponible en el PATH.

### 2. Instalar engines opcionales (solo si vas a usar operaciones reales)

Los engines son **dependencias runtime** del orquestrador. Si solo vas
a probar con `python_report` (built-in), no necesitas instalar nada más.

```bash
# Node.js + npm (para powerbi-modeling-mcp, superbi-mcp)
# macOS:   brew install node
# Linux:   apt install nodejs npm
# Windows: https://nodejs.org/

# Tabular Editor CLI (BPA)
# Windows/macOS: https://github.com/TabularEditor/TabularEditor/releases
# Linux: dotnet tool install --global TabularEditor
```

Los engines que el orquestrador **despacha** son esos tres, y son los
únicos que el `health` check reporta: si aparece ahí, alguna tool lo
usa. La validación PBIP que el orquestrador ejecuta es su propio
validador estructural, que no necesita binario externo.

Ver [`docs/engines-setup.md`](./docs/engines-setup.md) para detalles de
instalación por engine y troubleshooting.

### 3. Configurar el MCP client

Edita la config de tu MCP client (ej. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "powerbi-orchestrator-mcp": {
      "command": "powerbi-orchestrator-mcp",
      "args": ["--start"],
      "env": {
        "PBI_AUTH_MODE": "interactive",
        "PBI_WORKSPACE_ROOT": "/ruta/a/tus/pbip"
      }
    }
  }
}
```

`PBI_WORKSPACE_ROOT` acota las lecturas y las escrituras del
orquestador a ese directorio. Si no lo pones, se usa el directorio de
trabajo del proceso. Apunta ahí donde tengas tus `.pbip`: es la raíz
del workspace desde el punto de vista del agente.

Compatible con **VS Code + Copilot, Claude Desktop, OpenClaw, Hermes,
Claude Code, Cursor** y cualquier cliente MCP stdio.

### 4. Probar

En tu cliente MCP, el LLM ve **31 tools de alto nivel**, agrupadas por capa:

**Sesión y planificación**
- `connect_target` — abrir sesión contra un PBIP / Fabric workspace / PBI Desktop
- `plan_change` — crear un plan versionable
- `apply_plan` — ejecutar el plan con rollback

**Modelado semántico**
- `create_semantic_model_from_schema`, `add_measure_with_validation`,
  `refactor_to_calculation_groups`, `diff_models`, `generate_data_dictionary`

**Refactoring del modelo**
- `simplify_dax_measure` — reescribir una medida DAX en su forma corta equivalente
- `abstract_measures` — la lógica que está escrita más de una vez
- `abbreviate_names` — nombres de medidas y columnas que se han hecho demasiado largos

**Autoría de reportes**
- `create_report_from_dataset`, `edit_report_visual`,
  `design_report_page_from_requirements`, `select_visuals_for_kpis`,
  `screenshot_report_pages`, `optimize_report_performance`

**Nube Fabric / Power BI Service**
- `deploy_to_workspace`, `run_refresh`, `promote_in_pipeline`,
  `setup_rls_and_roles`, `set_sensitivity_labels`,
  `commit_workspace_to_git`, `sync_git_to_workspace`, `pre_deploy_check`,
  `execute_dax_query`

**Auditoría y calidad**
- `audit_model_and_report`, `audit_report_ux_and_storytelling`,
  `apply_theme_and_accessibility_rules`, `run_dax_regression`

**Diagnóstico**
- `powerbi_health`

Con `connect_target` + `plan_change` + `apply_plan` solos, el LLM ya puede
hacer safe_rename, audit, deploy y regression sobre cualquier PBIP local
(sin engines externos) o cualquier Fabric workspace (con
`powerbi-modeling-mcp` instalado).

## Tres flujos completos, de principio a fin

Los tres están en [`examples/`](./examples/), con los JSON de respuesta
esperados. Aquí va el esqueleto de cada uno.

### 1. Renombrar una columna sin romper nada

El flujo más importante para un analista: cambiar el nombre de una
columna y que las referencias del modelo **y** del reporte se muevan
juntas.

```
connect_target(
    target_type="pbip_folder",
    target_ref="/ruta/a/tus/sales.pbip"
)

plan_change(
    intent="safe_rename",
    options={
        "old_path": "FactSales[UnitPrice]",
        "new_path": "FactSales[Price]",
        "scope": "full"
    }
)

apply_plan(
    plan_id="<el que devolvió plan_change>",
    dry_run=false,
    confirm_each_step=false
)
```

`plan_change` devuelve un plan **versionable** (YAML) que puedes commitear
a Git antes de aplicarlo. `apply_plan` ejecuta los pasos con rollback
entre motores, y devuelve `partial` si el rollback no lo revirtió todo.
Después del rename, el paso de reporte dice **cuántas referencias
reescribió** — un rename que no encuentra nada en un PBIR real es un
dato, no un éxito.

→ [`examples/01-safe-rename/`](./examples/01-safe-rename/README.md)

### 2. Publicar en Fabric con el gate delante

El gate evalúa **findings**, no ficheros: primero auditas, y le pasas
el resultado a `pre_deploy_check` y a `deploy_to_workspace`.

```
audit_model_and_report(
    pbip_path="/ruta/a/tus/sales.pbip"
)

pre_deploy_check(
    findings_json="<los findings de la auditoría>",
    profile="strict"
)

deploy_to_workspace(
    pbip_path="/ruta/a/tus/sales.pbip",
    workspace_id="<workspace>",
    findings_json="<los mismos findings>",
    gate_profile="strict",
    dry_run=true          # ← empieza siempre por aquí
)

run_refresh(
    workspace_id="<workspace>",
    dataset_id="<dataset>",
    dry_run=true
)
```

`pre_deploy_check` **falla cerrado**: sin findings no pasa, y una
severidad que el perfil no conoce bloquea. Un despliegue bloqueado
igual lista las operaciones que habría hecho, para que el dry run siga
siendo informativo. `dry_run` viene en `true` por defecto en las dos
tools que escriben en la nube, así que hay que desactivarlo a
propósito.

→ [`examples/02-deploy-pbip/`](./examples/02-deploy-pbip/README.md)

### 3. Auditar, arreglar, volver a auditar

```
audit_model_and_report(
    pbip_path="/ruta/a/tus/sales.pbip"
)

apply_theme_and_accessibility_rules(
    pbip_path="/ruta/a/tus/sales.pbip",
    palette="okabe_ito",
    auto_backfill_alt_text=true
)

add_measure_with_validation(
    target="/ruta/a/tus/sales.pbip",
    table="FactSales",
    measure_name="Revenue",
    expression="SUM(FactSales[Price])"
)

audit_model_and_report(
    pbip_path="/ruta/a/tus/sales.pbip"
)   # segunda pasada
```

El bucle es el punto: auditar, corregir, auditar otra vez. La segunda
pasada es la que dice si el arreglo funcionó.

→ [`examples/03-audit-then-fix/`](./examples/03-audit-then-fix/README.md)

## Estado actual (v1.22.0)

- ✅ 31 tools implementadas (modelado, refactoring, reportes, nube, auditoría, UX, DAX)
- ✅ Cross-engine rollback
- ✅ Audit log encadenado con HMAC, con anchor firmado
- ✅ PlanBuilder con templates versionables
- ✅ Motores registrados: `python_report` y `python_modeling` (built-in),
  `powerbi-modeling-mcp`, `superbi-mcp`, `te` (Tabular Editor, para BPA).
  Cada paso dice **qué motor lo sirvió**
- ✅ Scopes por tool (las 31 declaran el suyo) y `Tools.Admin` para las
  operaciones de gobernanza
- ✅ Lecturas y escrituras acotadas al workspace (`PBI_WORKSPACE_ROOT`)
- ✅ Planes con dueño: conocer el `plan_id` no alcanza para leerlos ni
  borrarlos
- ✅ Story variance analysis (detección de regresiones visuales)
- ✅ mypy --strict clean, ruff clean, CI matrix Linux/macOS/Windows
- ✅ Publicado en PyPI: https://pypi.org/project/powerbi-orchestrator-mcp/

**Pendiente, y es deliberado:**

- ⏳ Los E2E contra un tenant real siguen siendo manuales
  (`pilot-validation.yml`, `workflow_dispatch`). Los secrets no existen
  todavía en el repo, así que automatizarlo sin tenant es teatro.
- ⏳ La propiedad de planes vive en un SQLite local, no en un IdP.
- ⏳ El audit log es *fail-open* por defecto: una escritura fallida no
  detiene la mutación, se reporta como aviso en el resultado. Con
  `PBI_AUDIT_FAIL_CLOSED=1` deja de serlo.

Ver [`RELEASE-NOTES-v1.20.0.md`](./RELEASE-NOTES-v1.20.0.md) para el detalle de
la línea 1.2x y [`CHANGELOG.md`](./CHANGELOG.md) para los cambios de la 1.22.0.

## Licencia

MIT.

## Atribución

- [`powerbi-modeling-mcp`](https://github.com/microsoft/powerbi-modeling-mcp) — Microsoft (EULA restrictiva)
- [`superbi-mcp`](https://github.com/cyphonica/superbi-mcp) — cyphonica (FSL, no commercial)
- [`te`](https://github.com/TabularEditor/TabularEditor) — Tabular Editor
- MCP spec: [modelcontextprotocol.io](https://modelcontextprotocol.io)
