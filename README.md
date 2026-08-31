# powerbi-orchestrator-mcp

> MCP server unificado y orquestador para Power BI / Fabric.
> Une modelado semántico, autoría de reportes, nube Fabric, validación y
> visualización/UX en un solo servidor delgado que delega a motores especializados.

**Status:** Specs v0.1 (agosto 2026). MVP en planificación.

---

## Qué es

Un servidor [Model Context Protocol](https://modelcontextprotocol.io) (stdio) que
expone **26 herramientas de alto nivel** (no 500 primitivas) para que un agente
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
- [`docs/architecture.md`](./docs/architecture.md) — arquitectura detallada.
- [`specs/`](./specs/README.md) — specs modulares por capa + por tool.
- [`docs/MVP-STATUS.md`](./docs/MVP-STATUS.md) — estado de implementación.
- [`docs/IMPLEMENTATION-PLAN-v1.0.md`](./docs/IMPLEMENTATION-PLAN-v1.0.md) — roadmap.

## Instalación (cuando esté implementado)

```bash
pip install powerbi-orchestrator-mcp
```

Configuración en cualquier MCP client:

```json
{"mcpServers":{"powerbi-orchestrator-mcp":{
  "command":"powerbi-orchestrator-mcp",
  "args":["--start"],
  "env":{"PBI_AUTH_MODE":"interactive"}
}}}
```

Compatible con VS Code + Copilot, Claude Desktop, OpenClaw, Hermes, Claude Code,
Cursor y cualquier cliente MCP stdio.

## Licencia

MIT.
