# Spec: Tool `sync_git_to_workspace` / `commit_workspace_to_git` (v3) — OUTLINE

> Integración bidireccional entre un workspace de Fabric / Power BI
> Service y un repo Git. Permite "save as commit" del workspace y
> "deploy from branch" desde Git, cerrando el loop de GitOps para BI.

**Status:** v0.1 (outline — sem 9-12 de implementación)
**Versión:** v3
**Capa:** 3 (Cloud)

> **Por qué v3, no v2:** Fabric tiene REST APIs para Git Integration
> (`/v1/workspaces/{id}/git/...`) que funcionan, pero la integración
> completa con PBIP local (roundtrip limpio con Desktop, manejo de
> conflicts, dry-runs que preserven ambos lados) tiene muchos edge
> cases. Decisión arquitectónica adoptada: empezar por `commit_workspace_to_git`
> (más simple, write-only) en lugar de `sync_git_to_workspace`
> (bidireccional con merge conflicts).

---

## Objetivo

Tomar un workspace de Fabric / Power BI Service y serializarlo a un
directorio PBIP commiteable a Git. La operación inversa (deploy desde
Git al workspace) es más compleja y queda para después de validar que
el sentido workspace→Git es estable.

Casos de uso:

1. **Snapshot del workspace:** "hicimos cambios manuales en Service
   desde la UI, commitealos a Git antes de seguir editando local".
2. **Backup automático:** cron que commitea el workspace nightly.
3. **Code review de cambios manuales:** el PR muestra el diff TMDL/PBIR
   de lo que cambió en el workspace.

## `commit_workspace_to_git` (v3 inicial)

### Inputs

- `workspace_id`: ID del workspace.
- `output_repo_path`: path al repo Git local.
- `branch`: opcional (default: rama actual).
- `commit_message`: opcional (default: `"Auto-commit from workspace <id> at <timestamp>"`).
- `exclude_items`: lista de item IDs a no incluir (ej. dataflows que no versionamos).
- `dry_run`: bool, default true.

### Outputs

- `items_committed`: lista de `{item_id, item_name, item_type, path}`.
- `commit_sha`: SHA del commit creado (None si dry_run).
- `conflicts_detected`: items que ya existían en Git con cambios divergentes.
- `large_file_warning`: items >50MB que pueden saturar Git.

### Comportamiento ante conflicts

Si el item existe en Git y tiene cambios sin commitear en el repo local
(o en commits posteriores al último sync):

1. **Sin cambios locales:** OK, sobrescribir.
2. **Con cambios locales no commiteados:** abort + elicitation (no resolvemos automáticamente).
3. **Con commits nuevos en la rama:** merge TMDL/PBIR si no hay conflicto textual; abort + elicitation si hay.

## `sync_git_to_workspace` (v3 posterior)

### Inputs

- `repo_path`: path al repo Git local.
- `branch_or_commit`: ref desde la cual deployar.
- `workspace_id`: destino.
- `conflict_resolution`: `prefer_workspace | prefer_git | manual` (default `manual`).

### Outputs

- `items_deployed`: lista de items deployados.
- `items_skipped`: items donde hubo conflict y se eligió skip.
- `pre_deploy_check_result`: el quality gate aplicado.

### Comportamiento ante conflicts

v3 inicial solo soporta `manual`: cualquier conflict aborta y elicita
al usuario con el diff lado-a-lado.

## Dependencias

- [`../01-orchestrator.md`](../01-orchestrator.md) — `plan_change` + `apply_plan` con rollback.
- [`../02-cloud-fabric.md`](../02-cloud-fabric.md) — endpoints de Git Integration.
- [`../03-validation.md`](../03-validation.md) — `pre_deploy_check` antes de cada deploy.
- [`../05-engines-adapters.md`](../05-engines-adapters.md) — modeling/report engines para merge TMDL/PBIR.
- Git CLI (`gitpython`) para operaciones de Git locales.

## Acceptance criteria

- [ ] `commit_workspace_to_git` serializa todos los items de un workspace a PBIP en un repo local.
- [ ] Commit resultante abre en Desktop sin warnings.
- [ ] Conflict detection: si un item fue modificado localmente desde el último sync, elicita.
- [ ] `pre_deploy_check` corre antes de cualquier `sync_git_to_workspace`.
- [ ] Audit log incluye `commit_sha` o `commit_attempted`.
- [ ] `--readonly` bloquea `sync_git_to_workspace` pero permite `commit_workspace_to_git` (que es read del workspace + write a filesystem local).

## Riesgos / open questions

- **Git Integration de Fabric ya existe**: ¿competimos con eso? Decisión: no, nuestro valor agregado es (a) merge TMDL/PBIR inteligente, (b) commit granular por item, (c) integration con el resto del orquestador (planes, rollback, audit).
- **Items grandes en Git**: workspaces con datasets >100MB son problemáticos para Git. Default: warning + opt-in para Git LFS.
- **Dataflows Gen2 vs Dataflows Gen1**: formato distinto, comportamiento de Git distinto. ¿Manejamos ambos? Propuesta v3 inicial: solo datasets, reports, y dataflows Gen1. Gen2 v3.1.
- **Permissions**: ¿qué pasa si el SPN no tiene acceso Git Integration en el workspace? Documentar scopes.
- **Roundtrip con Desktop**: commit a Git → checkout en Desktop → edit → push → debe funcionar sin warnings. Test e2e crítico.

## Fuera de alcance (v3)

- ❌ Pull requests UI integration (asumimos CLI Git).
- ❌ Branching automático (ej. "rama por feature").
- ❌ Code owners / reviewers enforcement.
- ❌ Merge de branches divergentes (solo fast-forward o abort).
- ❌ Items no soportados por Git Integration de Fabric (Notebooks, Spark jobs).

---

## Spec completo

Detalle (Pydantic models del conflict resolution, integración con Git
Integration API de Fabric, merge TMDL/PBIR por content-based diff) en
Semana 9-12.
