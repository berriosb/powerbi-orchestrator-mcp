# Spec: GitHub Actions Publish Workflow

> Diseño del workflow que automatiza la publicación de
> `powerbi-orchestrator-mcp` a PyPI desde CI. Reemplaza el flujo
> documentado en `CONTRIBUTING.md` §"Release process" paso 5 que
> **actualmente no existe** — solo `verify.yml` está implementado.

**Status:** v0.1 (spec)
**Prioridad:** P1 — bloqueante para v1.0 production-ready (releases
automatizados reducen error humano y permiten audit trail en GitHub)
**Responsable:** Bastian Berrios
**Depende de:** [`CONTRIBUTING.md` §"Release process"](../CONTRIBUTING.md)
**Habilita:** releases one-click vía GitHub UI; menos dependencia de
`twine` local y del token en `~/.pypirc`

---

## Cambios respecto a v0.0

N/A (spec inicial — no existe workflow de publish todavía).

---

## 1. Objetivo

Diseñar `.github/workflows/publish.yml` que:

1. Construya wheel + sdist desde una tag `vX.Y.Z` o vía
   `workflow_dispatch` manual.
2. Suba primero a **TestPyPI** para smoke test (configurable: skip
   con input `promote=true`).
3. Promueva a **PyPI real** después de verificar el TestPyPI install.
4. Cree una **GitHub Release** con las notes del tag + checksums
   SHA256 de los artefactos.
5. Mantenga audit trail completo en GitHub Actions.

**Métricas de éxito:**

- Una release `vX.Y.Z` se publica end-to-end sin tocar la terminal.
- `pip install powerbi-orchestrator-mcp==vX.Y.Z` desde PyPI funciona
  inmediatamente después de que el workflow termina.
- El SHA256 del wheel publicado matchea el del artefacto subido (la
  metadata de PyPI no se manipula post-upload).

---

## 2. Triggers

### 2.1 Trigger primario: tag push

```yaml
on:
  push:
    tags:
      - 'v[0-9]+.[0-9]+.[0-9]+'   # solo tags semver, no 'v1.9' ni 'v1'
```

Por qué tag-push:

- El tag ya es el "release marker" semántico — un workflow que
  dispara en tag es idiomático.
- Requiere que el tag se cree manualmente (con `git tag -a vX.Y.Z -m
  "..." && git push origin vX.Y.Z` desde local) — preserva el
  control humano sobre qué se publica.
- Si el push se hace a una rama, no se publica — el `verify.yml`
  sigue corriendo en cada PR para los tests de rigor.

### 2.2 Trigger secundario: manual `workflow_dispatch`

```yaml
on:
  workflow_dispatch:
    inputs:
      version:
        description: 'Version to publish (e.g., 1.9.1)'
        required: true
      promote:
        description: 'Skip TestPyPI, go straight to PyPI'
        type: boolean
        default: false
      ref:
        description: 'Git ref to build from (branch/tag/sha). Defaults to the tag matching `version`.'
        required: false
```

Por qué manual:

- Permite re-publicar un release específico (corrección de último
  momento) sin tener que re-tag.
- Permite a un colaborador con permisos de GitHub Actions disparar
  la release sin acceso al repo local ni al token de PyPI.
- Input `promote=true` se usa cuando el tag ya fue probado
  manualmente en TestPyPI y se quiere saltar la verificación (log
  queda registrado de quién lo skipeó).

---

## 3. Permisos y secretos

### 3.1 Permisos del job

```yaml
permissions:
  contents: write     # crear GitHub Release
  id-token: write     # trusted publishing OIDC (alternativa a API token)
```

### 3.2 Secret requeridos

| Secret | Uso | Cómo obtenerlo |
|---|---|---|
| `PYPI_API_TOKEN` | Token clásico de PyPI para `twine upload` | https://pypi.org/manage/account/token/ — scope "Entire account" (no project-specific; PyPI no lo soporta para cuenta propia) |
| `TEST_PYPI_API_TOKEN` | Token de TestPyPI para smoke test | https://test.pypi.org/manage/account/token/ — mismo proceso |

### 3.3 Alternativa: Trusted Publishing (OIDC)

PyPI soporta "trusted publishing" vía OIDC de GitHub Actions desde 2023:
no requiere secret, el token se emite dinámicamente. Requiere:

1. Configurar en https://pypi.org/manage/account/publishing/ el
   publisher "GitHub Actions" para el repo
   `berriosb/powerbi-orchestrator-mcp`.
2. Reemplazar `twine upload` por `pypa/gh-action-pypi-publish` con
   `password: ${{ github.token }}`.

**Decisión (recomendada):** empezar con API token (más simple,
debuggeable), migrar a OIDC en una release futura cuando el flujo
está maduro. Documentar ambos approaches en este spec.

---

## 4. Jobs

### Job 1: `build`

```yaml
build:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
      with:
        fetch-depth: 0   # necesario para tags y changelog generation
    - uses: actions/setup-python@v5
      with:
        python-version: '3.12'
        cache: 'pip'
    - name: Install build tools
      run: python -m pip install --upgrade build twine
    - name: Build wheel + sdist
      run: python -m build
    - name: Verify metadata
      run: python -m twine check dist/*
    - name: Upload artifacts
      uses: actions/upload-artifact@v4
      with:
        name: dist
        path: dist/*
        retention-days: 14
```

**Outputs:** `dist/*.whl`, `dist/*.tar.gz` (subidos como artifact para
que los jobs siguientes los descarguen).

### Job 2: `test-pypi` (gate condicional)

```yaml
test-pypi:
  needs: build
  if: github.event_name == 'push' || (github.event_name == 'workflow_dispatch' && inputs.promote != 'true')
  runs-on: ubuntu-latest
  steps:
    - uses: actions/download-artifact@v4
      with:
        name: dist
        path: dist
    - name: Publish to TestPyPI
      env:
        TWINE_USERNAME: __token__
        TWINE_PASSWORD: ${{ secrets.TEST_PYPI_API_TOKEN }}
      run: python -m twine upload --skip-existing dist/*
    - name: Smoke install from TestPyPI
      run: |
        python -m venv /tmp/smoke
        /tmp/smoke/bin/pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            powerbi-orchestrator-mcp==${{ github.ref_name }} || \
        /tmp/smoke/bin/pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            powerbi-orchestrator-mcp==${{ inputs.version }}
        /tmp/smoke/bin/python -c "import powerbi_orchestrator_mcp; print(powerbi_orchestrator_mcp.__version__)"
```

**Por qué TestPyPI:** permite verificar que el wheel se instala y
arranca sin corromper el índice público. El `--extra-index-url` es
necesario porque TestPyPI no tiene todas las dependencias transitivas
(azure-identity, httpx, etc.).

### Job 3: `pypi` (release final)

```yaml
pypi:
  needs: [build, test-pypi]
  if: github.event_name == 'push' || (github.event_name == 'workflow_dispatch' && inputs.promote == 'true')
  runs-on: ubuntu-latest
  environment: pypi-production    # protegido por required reviewers
  steps:
    - uses: actions/download-artifact@v4
      with:
        name: dist
        path: dist
    - name: Compute checksums
      run: sha256sum dist/* > SHA256SUMS
    - name: Publish to PyPI
      env:
        TWINE_USERNAME: __token__
        TWINE_PASSWORD: ${{ secrets.PYPI_API_TOKEN }}
      run: python -m twine upload --skip-existing dist/*
    - name: Verify on PyPI
      run: |
        sleep 30   # dar tiempo al CDN
        curl -sf https://pypi.org/pypi/powerbi-orchestrator-mcp/${{ github.ref_name || inputs.version }}/json \
          | python -c "import json,sys; d=json.load(sys.stdin); assert d['info']['version']=='${{ github.ref_name || inputs.version }}', 'version mismatch'; print('verified')"
```

### Job 4: `github-release`

```yaml
github-release:
  needs: pypi
  if: success()
  runs-on: ubuntu-latest
  permissions:
    contents: write
  steps:
    - uses: actions/download-artifact@v4
      with:
        name: dist
        path: dist
    - name: Extract release notes
      run: |
        VERSION="${{ github.ref_name || inputs.version }}"
        # busca el archivo RELEASE-NOTES-vX.Y.Z.md y extrae el body
        if [ -f "RELEASE-NOTES-${VERSION}.md" ]; then
          # strip el H1 inicial (ya está en el título del release)
          tail -n +3 "RELEASE-NOTES-${VERSION}.md" > /tmp/release-body.md
        else
          echo "RELEASE-NOTES-${VERSION}.md not found, using commit log" >&2
          git log $(git describe --tags --abbrev=0 ${VERSION}^ 2>/dev/null)..${VERSION} --pretty=format:"- %s" > /tmp/release-body.md
        fi
    - name: Create GitHub Release
      uses: softprops/action-gh-release@v2
      with:
        tag_name: ${{ github.ref_name }}
        name: 'Release ${{ github.ref_name }}'
        body_path: /tmp/release-body.md
        files: |
          dist/*.whl
          dist/*.tar.gz
          SHA256SUMS
        generate_release_notes: false
        draft: false
        prerelease: false
```

---

## 5. Environments

Crear dos environments en GitHub repo settings:

1. **`pypi-test`** — para el job `test-pypi`. Sin reviewers required.
2. **`pypi-production`** — para el job `pypi`. **Required reviewers:**
   mínimo 1 (idealmente 2 con uno siendo un colaborador externo).
   Protección contra push directo accidental.

Esto bloquea la promoción a PyPI real sin aprobación humana — útil
para evitar publish accidentales.

---

## 6. Actualización de `CONTRIBUTING.md`

Tras implementar el workflow, reescribir §"Release process":

```markdown
## Release process

1. **Pick a version.** We follow semver (patch / minor / major).
2. **Bump** in `pyproject.toml`.
3. **Update** `CHANGELOG.md` and create `RELEASE-NOTES-vX.Y.Z.md`.
4. **Commit + tag + push:**
   ```bash
   git commit -am "chore(release): vX.Y.Z — <summary>"
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   git push origin main --follow-tags
   ```
5. **GitHub Actions** (workflow `publish.yml`) takes over:
   - Builds wheel + sdist.
   - Publishes to TestPyPI, runs smoke install.
   - Publishes to PyPI (requires reviewer approval on
     `pypi-production` environment).
   - Creates GitHub Release with `RELEASE-NOTES-vX.Y.Z.md` body.
   - Uploads wheels + SHA256SUMS as release assets.
6. **Announce** (Twitter / Discord / etc.).

Para re-publicar o promover sin TestPyPI:
- Ir a Actions → publish.yml → Run workflow.
- Inputs: `version`, opcionalmente `promote=true` (omite TestPyPI).
```

---

## 7. Acceptance criteria

- [ ] `.github/workflows/publish.yml` existe con los triggers §2 y
      jobs §4.
- [ ] El job `build` produce artefactos válidos (`twine check` OK).
- [ ] El job `test-pypi` smoke-installa desde TestPyPI con exit 0.
- [ ] El job `pypi` requiere approval del environment
      `pypi-production`.
- [ ] Tras un release de prueba `v0.0.0-test`, el proyecto PyPI real
      tiene un release visible con descripción correcta.
- [ ] El job `github-release` adjunta `.whl`, `.tar.gz` y
      `SHA256SUMS` al release.
- [ ] `CONTRIBUTING.md` §"Release process" reescrito según §6.
- [ ] Secrets `PYPI_API_TOKEN` y `TEST_PYPI_API_TOKEN` configurados
      en repo settings (no en el código).
- [ ] Tag push a un branch `release/vX.Y.Z` no dispara el workflow
      (solo tags `vX.Y.Z` estrictos).

---

## 8. Out of scope (MVP del workflow)

- ❌ Trusted publishing OIDC (cubierto en §3.3, queda para v2).
- ❌ Auto-bump de `pyproject.toml` (requeriría un bot o semantic-release
  action; introduce riesgo de drift entre tag y version).
- ❌ Publicación a mirrors adicionales (Artifactory interno, etc.).
- ❌ Notificaciones Slack/Discord automáticas.

---

## 9. Riesgos

| Riesgo | Mitigación |
|---|---|
| Push a tag accidental publica una versión rota | Environment `pypi-production` con required reviewers. Tags solo desde local con `git push origin vX.Y.Z` (no desde PRs). |
| Token de PyPI filtrado | Rotar inmediatamente. Migrar a OIDC cuando sea estable. |
| TestPyPI smoke install falla por dependencia faltante | El `--extra-index-url https://pypi.org/simple/` ya está en §4 job 2. Documentado en troubleshooting. |
| El job `pypi` falla después de que `test-pypi` pasó | Reintentar via `workflow_dispatch` con mismo `version` (idempotente por `--skip-existing`); investigar root cause antes de reintentar. |
| SHA256 mismatch entre artifact y PyPI | PyPI no permite re-upload del mismo filename. Si hay mismatch, hay que yank + re-release con nuevo filename — cubierto por [`supersede-policy.md`](./supersede-policy.md). |
| Build time excesivo (>5min) | Cache de pip via `setup-python@v5`. Wheel es chico (~200KB) así que el build es rápido. |

---

## 10. Specs relacionados

- [`supersede-policy.md`](./supersede-policy.md) — define qué hacer
  cuando el workflow automático falla o cuándo yank.
- [`CONTRIBUTING.md` §"Release process"](../CONTRIBUTING.md) — la
  sección a reescribir tras implementar este spec.
- [`specs/qa/e2e-testing-strategy.md`](../qa/e2e-testing-strategy.md) —
  los smoke tests deberían correr también en este workflow antes del
  job `pypi`.