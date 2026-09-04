# powerbi-orchestrator-mcp

> **Un servidor MCP orquestrador** que unifica modelado semántico, autoría
> de reportes, nube Fabric, validación y visualización/UX para Power BI /
> Fabric en **26 herramientas de alto nivel** (no 500 primitivas).
>
> El orquestrador **delega** a motores especializados (subprocess) y
> presenta al LLM una superficie coherente y de alto nivel.

**Status:** v1.0.0 released ([release notes](./RELEASE-NOTES-v1.0.0.md)) — MVP done (15/15 tools: 12 MVP + 3 v1.1).

---

## Arquitectura de 3 capas (importante)

El proyecto **NO** es un wrapper sobre los MCP servers existentes. Es un
servidor MCP propio que **consume** otros MCP servers como subprocess.
Esto es lo que permite presentar al LLM 26 tools coherentes en lugar
de 500 primitivas dispersas.

```
┌─────────────────────────────────────────────────────────────────┐
│ Capa 1: MCP Client (Claude Desktop, VS Code, Copilot, Cursor)    │
│         Habla JSON-RPC sobre stdio con el orquestrador.          │
│         El LLM ve 26 tools de alto nivel.                        │
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
│         dscmd (DAX Studio)      → Windows binary                  │
│         pbip-validator          → pip: pip install pbip-validator │
│         python_report           → built-in (parte del orquestrador)│
└─────────────────────────────────────────────────────────────────┘
```

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

- [`SPEC.md`](./SPEC.md) — visión, arquitectura 6 capas, MVP ambicioso.
- [`RELEASE-NOTES-v0.1.0.md`](./RELEASE-NOTES-v0.1.0.md) — qué incluye v0.1.0.
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

El comando `powerbi-orchestrator-mcp` queda disponible en el PATH.

### 2. Instalar engines opcionales (solo si vas a usar operaciones reales)

Los engines son **dependencias runtime** del orquestrador. Si solo vas
a probar con `python_report` (built-in), no necesitas instalar nada más.

```bash
# Node.js + npm (para powerbi-modeling-mcp, superbi-mcp)
# macOS:   brew install node
# Linux:   apt install nodejs npm
# Windows: https://nodejs.org/

# Tabular Editor CLI (modeling fallback + BPA)
# Windows/macOS: https://github.com/TabularEditor/TabularEditor/releases
# Linux: dotnet tool install --global TabularEditor

# pbip-validator (Microsoft, cuando esté publicado)
pip install pbip-validator

# DAX Studio (Windows only)
# https://daxstudio.org/
```

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
      "env": {"PBI_AUTH_MODE": "interactive"}
    }
  }
}
```

Compatible con **VS Code + Copilot, Claude Desktop, OpenClaw, Hermes,
Claude Code, Cursor** y cualquier cliente MCP stdio.

### 4. Probar

En tu cliente MCP, el LLM ahora ve 3 tools (MVP):
- `connect_target` — abrir sesión contra un PBIP / Fabric workspace / PBI Desktop
- `plan_change` — crear un plan versionable
- `apply_plan` — ejecutar el plan con rollback

Con esos 3, el LLM puede hacer safe_rename, audit, deploy, y regression
sobre cualquier PBIP local (sin engines externos) o cualquier Fabric
workspace (con `powerbi-modeling-mcp` instalado).

## Estado actual (v0.1.0)

- ✅ 3 tools MVP funcionando (connect/plan/apply)
- ✅ Cross-engine rollback
- ✅ Audit log con HMAC chain
- ✅ PlanBuilder con 4 templates MVP
- ✅ Engine adapters: `python_report` (built-in), `powerbi-modeling-mcp`,
  `superbi-mcp` (mock-tested)
- ✅ 374 tests, 89% coverage, mypy --strict clean, ruff clean
- ⏳ Pendiente Week 2: `te`, `dscmd`, `pbip-validator` adapters
- ⏳ Pendiente: tests con binaries reales

Ver [`RELEASE-NOTES-v0.1.0.md`](./RELEASE-NOTES-v0.1.0.md) para detalles completos.

## Licencia

MIT.

## Atribución

- [`powerbi-modeling-mcp`](https://github.com/microsoft/powerbi-modeling-mcp) — Microsoft (EULA restrictiva)
- [`superbi-mcp`](https://github.com/cyphonica/superbi-mcp) — cyphonica (FSL, no commercial)
- [`te`](https://github.com/TabularEditor/TabularEditor) — Tabular Editor
- MCP spec: [modelcontextprotocol.io](https://modelcontextprotocol.io)
