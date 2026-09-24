# Spec: PyPI Release Supersede & Yank Policy

> Política de versionado y marcado de releases en PyPI para
> `powerbi-orchestrator-mcp`. Decide cuándo un release nuevo invalida
> al anterior y cuándo corresponde "yank" (no-installable) vs dejar
> visible.

**Status:** v0.1 (spec)
**Prioridad:** P1 — baja urgencia pero documenta una decisión que ya
está pendiente (v1.9.0 vs v1.9.1)
**Responsable:** Bastian Berrios
**Depende de:** [`CONTRIBUTING.md`](../CONTRIBUTING.md) §"Release process"
**Habilita:** consistencia en cómo se ve la página de PyPI y qué hace
`pip install` por default

---

## Cambios respecto a v0.0

N/A (spec inicial).

---

## 1. Objetivo

Definir la política que decide:

1. **Cuándo un release nuevo invalida al anterior** — y por tanto debe
   marcarse como superseded (vía descripción, project URLs, o yank).
2. **Cuándo corresponde yank** (ocultar de `pip install`) vs simplemente
   dejar el release antiguo visible.
3. **Qué se muestra en la página de PyPI** del paquete: latest,
   superseded, yanked.

**Métricas de éxito:**

- Usuario que hace `pip install powerbi-orchestrator-mcp` siempre obtiene
  el release más nuevo que funcione, salvo que pida uno específico.
- Un release con bug crítico confirmado se puede yank sin romper
  `pip install powerbi-orchestrator-mcp` para usuarios existentes.
- La descripción en la página de PyPI siempre refleja el estado real
  del release (no dice "pendiente publicación" cuando ya está publicado).

---

## 2. Conceptos PyPI relevantes

**Latest release (default):** el botón "Latest" en PyPI apunta al
release más alto por SemVer que NO esté yanked. `pip install <pkg>` sin
pin de versión resuelve a este.

**Yanked release:** sigue visible en el historial del proyecto (con
etiqueta amarilla) pero `pip install <pkg>==<yanked>` falla salvo que
se use `--no-deps` y `--ignore-requires-python` o se pida
explícitamente con pinning. NO se puede yank un release que tenga
dependientes (PyPI lo bloquea para evitar romper el ecosystem).

**Supersede "soft":** no existe como concepto formal en el índice. Se
consigue vía la metadata del proyecto:
- `long_description` del latest release puede mencionar "supersedes
  vX.Y.Z"
- `project_urls` en `pyproject.toml` puede apuntar a un CHANGELOG que
  muestre el historial completo
- GitHub Releases con `is_prerelease: false` para el latest

**Display settings del proyecto PyPI (admin-only):** controlan badges,
"yanked reason", etc. Cambian a través del formulario web de PyPI, no
vía CLI.

---

## 3. Casos y decisiones

### Caso A: release nuevo es estrictamente superior al anterior

**Trigger:** el release nuevo fija bugs, actualiza docs, o mejora
metadata sin breaking changes. **Decisión:** **NO yank el anterior.**

Justificación:
- El release anterior sigue siendo funcional (código que se ejecuta
  correctamente).
- Yankear rompe a usuarios que tengan pin `==1.9.0` o rangos como
  `>=1.9.0,<1.9.2` en sus requirements.txt.
- El usuario que instale sin pin obtiene el nuevo vía el redirect
  "Latest" → no le afecta.
- Yank está pensado para casos de seguridad o funcionalidad rota, no
  para "hay una versión mejor".

**Acción concreta:**
- En el `long_description` del release nuevo, mencionar
  "Supersedes vX.Y.Z — see CHANGELOG".
- En `pyproject.toml`, mantener `project_urls` apuntando al CHANGELOG.
- En GitHub Releases, marcar el release nuevo como latest.

### Caso B: release anterior tiene bug crítico de seguridad

**Trigger:** CVE publicado, o pérdida de datos confirmada, o auth bypass.
**Decisión:** **YANK inmediato del release afectado.**

Justificación:
- Yankear es la única forma de impedir que `pip install <pkg>` (sin
  pin) devuelva la versión vulnerable a un usuario nuevo.
- Sí rompe pins, pero el costo de un bug de seguridad sin parchear es
  mayor que el costo de un usuario que tenga que actualizar pin.

**Acción concreta:**
- `twine upload --skip-existing` no funciona para yank; usar el panel
  web de PyPI (admin) o el API REST de PyPI con un token de admin.
- Marcar `yanked_reason` con un texto corto: "Security: CVE-XXXX-XXXXX.
  Upgrade to vX.Y.Z+."
- Publicar advisory (GitHub Security Advisory) con la CVE.
- En el release nuevo (post-fix), incluir "Security: addresses
  CVE-XXXX-XXXXX" en el changelog.

### Caso C: release anterior tiene README / metadata desactualizada
pero código funcional

**Trigger:** v1.9.0 del 2026-09-14 — README decía "pendiente
publicación en PyPI" después de publicado, descripción drift. El código
funciona.
**Decisión:** **NO yank. Patch release (v1.9.1) con metadata corregida.**

Justificación:
- El problema es cosmético (descripción en PyPI), no funcional.
- Yankear obligaría a usuarios con pin `==1.9.0` a actualizar.
- Un patch release resuelve sin romper nada.
- Costo: 1 wheel nuevo de ~192 KB en PyPI.

**Acción concreta:**
- Bump patch + rebuild + `twine upload`.
- Verificar que la `long_description` del nuevo release reemplaza a la
  del anterior en el display "Latest".
- Si PyPI sigue mostrando la descripción del anterior en la pestaña
  "Release history" (algo que pasa), agregar un disclaimer en el
  `long_description` del nuevo: "Note: vX.Y.Z description below
  supersedes the prior vX.Y.W upload."

---

## 4. Política consolidada (decision tree)

```
¿El release anterior tiene un bug crítico de seguridad confirmado?
├── Sí  → YANK + publicar advisory + bump patch con fix
└── No
    ├── ¿El código del release anterior funciona correctamente?
    │   ├── Sí  → NO yank. Patch release con fix de metadata/docs.
    │   └── No  → YANK + bump patch con fix + comunicarlo en CHANGELOG.
    └── ¿El cambio es puramente cosmético (descripción, docs)?
        └── Sí  → NO yank. Patch release.
```

**Regla práctica:** el yank es la excepción, no la regla. Antes de
yankear, evaluar si un patch release alcanza.

---

## 5. Configuración de `pyproject.toml`

Independientemente del caso, `pyproject.toml` debe mantener
`project_urls` para que el sidebar de PyPI muestre contexto:

```toml
[project.urls]
Homepage = "https://github.com/berriosb/powerbi-orchestrator-mcp"
Repository = "https://github.com/berriosb/powerbi-orchestrator-mcp"
Issues = "https://github.com/berriosb/powerbi-orchestrator-mcp/issues"
Changelog = "https://github.com/berriosb/powerbi-orchestrator-mcp/blob/main/CHANGELOG.md"
Releases = "https://github.com/berriosb/powerbi-orchestrator-mcp/releases"
```

(verificar tras la próxima release que el sidebar efectivamente los
renderiza; algunos campos requieren "verified domain" en GitHub para
mostrarse como link azul en vez de gris).

---

## 6. Decisión actual: v1.9.0 vs v1.9.1

**Aplica Caso C (release anterior funciona, solo metadata drift).**

- v1.9.0: NO yank. Funcional, instalable, sigue siendo el primer release
  público del proyecto (hito histórico).
- v1.9.1: nuevo "Latest". Description corregida.
- En el `long_description` de v1.9.1 (enviado en este release):
  mencionar "Supersedes v1.9.0 (README metadata fix — see CHANGELOG)."

**Trade-off conocido:** la pestaña de historial en PyPI mostrará la
descripción vieja de v1.9.0 si alguien navega explícitamente a
https://pypi.org/project/powerbi-orchestrator-mcp/1.9.0/. Eso es
aceptable: cualquier usuario que llegue ahí sabrá leer el CHANGELOG
para entender la transición.

---

## 7. Acceptance criteria

- [ ] Este spec existe y está enlazado desde `CONTRIBUTING.md`
      §"Release process".
- [ ] `pyproject.toml` tiene `project_urls` con Changelog + Releases.
- [ ] Decisión documentada para v1.9.0 vs v1.9.1 en
      `RELEASE-NOTES-v1.9.1.md` (ya hecho).
- [ ] No hay releases yanked en
      https://pypi.org/project/powerbi-orchestrator-mcp/#history
      después de v1.9.1.
- [ ] Política revisada cada 6 meses o ante un incidente de seguridad.

---

## 8. Out of scope (MVP)

- ❌ Firma digital de wheels (`sign` con `twine upload --sign`).
  Requiere GPG key mantenida fuera del repo; no crítico para beta.
- ❌ Trusted publishing via OIDC (reemplaza el `PYPI_API_TOKEN` por
  tokens de GitHub Actions). Sería un win de seguridad pero depende del
  workflow de [`specs/ci/publish-workflow.md`](./ci/publish-workflow.md).
- ❌ Pre-releases en PyPI (`a1`, `rc1`). El proyecto aún no tiene
  audiencia para betas públicos.

---

## 9. Riesgos

| Riesgo | Mitigación |
|---|---|
| Yank rompe `requirements.txt` de un usuario downstream | El usuario recibe error claro de pip apuntando a la versión yanked. Comunicar en el CHANGELOG y en GitHub Release notes con "Upgrade path: bump to vX.Y.Z". |
| El panel admin de PyPI para yank no está claro / no lo encontramos | Documentar los pasos exactos en este spec (sección 2) y crear un script `scripts/yank_pypi_version.sh` si la acción se repite. |
| Display settings de PyPI cambian sin aviso | Snapshot anual del estado del proyecto PyPI (URL + descripción + project URLs) guardado en `docs/pypi-snapshot-YYYY.md`. |
| Versión yanked no se puede deshacer fácilmente | PyPI permite un-yank, pero queda en el historial. Documentar antes de yankear. |

---

## 10. Specs relacionados

- [`CONTRIBUTING.md` §"Release process"](../CONTRIBUTING.md) — describe
  los pasos de release; este spec define qué hacer cuando un release
  nuevo sale.
- [`CHANGELOG.md`](../CHANGELOG.md) — debe reflejar cada decisión de
  supersede / yank.
- [`specs/ci/publish-workflow.md`](./ci/publish-workflow.md) —
  automatiza la subida; este spec define qué hacer cuando el workflow
  automático se equivoca.