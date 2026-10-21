# Release Notes — v1.23.0

**Fecha:** 2026-10-21
**Tipo:** Minor — los tres controles que faltaban para declarar un despliegue "listo"

> Un release que responde a una pregunta concreta: **si mañana esto
> va a producción, ¿qué impide que alguien lo use mal?** No es un
> release de funcionalidad: es el de los controles que permiten decir
> que un despliegue está configurado como se dice que está.

---

## 🔒 Un servidor en solo-lectura

`PBI_READONLY=1`, o `--readonly` en la línea de comandos, rechaza
**toda tool que modifique estado**.

```bash
powerbi-orchestrator-mcp --readonly
```

Lo importante es *dónde* se comprueba: en `authorize_tool`, antes que
los scopes. Es una propiedad del **despliegue**, no del llamador, así
que un principal con `Tools.Admin` tampoco puede saltárselo:

```
Error executing tool deploy_to_workspace: tool 'deploy_to_workspace' is
state-changing and the server is in read-only mode (PBI_READONLY is set).
```

Un solo punto de comprobación cubre las 20 tools mutantes, porque todas
declaran su scope en el mismo mapa. Y el cuerpo de la tool no se
ejecuta nunca: la comprobación va antes del body.

Es el switch que hace falta para poner un LLM contra un workspace real
sin que pueda cambiar nada — y para que esa garantía no dependa de la
buena fe del prompt.

---

## 🕵️ La auditoría dice *quién*

Cada entrada del audit log lleva ahora el actor, en una **columna
propia**:

| Transporte | Actor |
|---|---|
| HTTP | El principal ligado (`upn`, `oid`, `sub`…) |
| stdio | El usuario del sistema operativo que corre el proceso, marcado `local:` |

Sobre stdio no hay principal, pero sí hay una persona: la que arrancó
el proceso. "Sin principal" no es "sin atribución".

### Por qué una columna y no el payload

El payload se redacta: un email se convierte en un hash `sha256[:8]`.
Una UPN **es** un email, así que el actor se habría guardado como un
hash — distinguible, pero inatribuible. La columna va aparte, y **no se
redacta**.

Y está dentro de la cadena: el `principal` forma parte de los campos
hasheados de la fila, así que **retocarlo después rompe la cadena**.
Hay un test que lo comprueba.

### Un log anterior sigue verificando

Una fila sin principal hashea los cinco campos de antes. Un audit log
escrito antes de la columna sigue verificando después de la
actualización, en vez de fallar en la primera fila.

---

## 📋 El despliegue se reporta a sí mismo

Al arrancar, y en `powerbi_health`, el servidor dice cómo está
configurado:

| Finding | Dice |
|---|---|
| `read_boundary` | Si las lecturas están acotadas al workspace |
| `workspace_root` | Dónde está el root, o que ha caído al CWD |
| `transport` | Si el transporte hace que los scopes signifiquen algo |
| `audit_fail_closed` | Si el audit log es precondición o informe |
| `audit_secret` | Si la clave de la cadena está fuera de esta máquina |
| `readonly` | En qué modo arrancó |
| `orchestrator_home` | Si se puede escribir donde tiene que escribir |
| `bpa_check` | Si hay reglas de BPA configuradas |
| `plan_store` | Que esto es un nodo único |

Un despliegue que apagó la frontera de lectura **lo dice**. Es la
diferencia entre "no tenemos el dato" y "el dato dice que no lo
tenemos".

---

## 🔧 La invocación de BPA no correspondía a ningún Tabular Editor

Esta es la que más duele, porque era invisible.

El runner invocaba:

```
te bpa <modelo> --ruleset <nombre> --format json
```

**No corresponde a ningún producto real.** Y el esquema que parseaba
(`{"score": 87.5, "findings": [{"RuleId": ...}]}`) tampoco.

Hay dos productos distintos, y no hablan el mismo CLI:

- **Tabular Editor 2** — `TabularEditor.exe`, gratis, solo Windows. BPA
  con `-A <rules.json>` y salida SARIF con `-G <report.sarif>`. **Es el
  que el proyecto descarga y fija por SHA256.**
- **Tabular Editor CLI** — `te`, más nuevo, multiplataforma. Otra forma
  de comando (`te bpa run --model ... --output-format json`), requiere
  una cuenta de Tabular Editor para descargarlo, y **el proveedor avisa
  que el preview deja de funcionar el 2026-10-31**.

El runner ahora habla el CLI de Tabular Editor 2, que es el producto
que este proyecto puede obtener de verdad:

```
TabularEditor.exe <modelo.bim> -A <rules.json> -G <report.sarif>
```

y parsea el SARIF, que es un formato estable y documentado.

### Sin reglas, no hay corrida

Tabular Editor corre **las reglas de la máquina en la que corre**. En un
servidor no hay ninguna. Una corrida sin archivo de reglas devolvería
"100, sin hallazgos": un verde falso.

Así que el runner **se niega a correr**:

```
no BPA rules file configured, so BPA was not run. Tabular Editor runs
the rules of the machine it runs on, and a server has none: the result
would be a clean score that means nothing. Set PBI_TE_BPA_RULES to a
rules JSON file (a path or a URL), or pass custom_ruleset.
```

Sin reglas no es un check que pasa: es un check que no corre, y el
audit lo reporta como omitido.

### Un test que lo ejecuta de verdad

`tests/e2e/test_te_real_binary.py` invoca el binario real contra el
fixture y comprueba que la cadena entera funciona: binario → reglas →
SARIF → findings. Corre en el runner Windows del nightly.

El archivo de reglas del smoke test
(`tests/fixtures/bpa_rules.json`) contiene una regla que casa con
toda medida, así que un finding demuestra que todo el recorrido
funcionó.

---

## 📚 Documentación

- `docs/engines-setup.md` explica la diferencia entre Tabular Editor 2
  y el Tabular Editor CLI, y que el segundo no es una dependencia.
- `SECURITY.md` y el README documentan el modo solo-lectura, el actor
  en la auditoría y los findings de readiness.

---

## Lo que sigue igual

Este release añade controles. **No cambia los límites de despliegue**,
que siguen siendo:

- **Un solo nodo.** El plan store es un SQLite local.
- **Los scopes fallan open sobre stdio.** Se encienden con
  `PBI_TRANSPORT=http` + Entra ID.
- **El camino cloud sin validar contra un tenant real.**
  `pilot-validation.yml` está listo y solo espera credenciales:
  `PBI_PILOT_TENANT_ID`, `PBI_PILOT_CLIENT_ID`,
  `PBI_PILOT_CLIENT_SECRET` y las variables `PBI_PILOT_WORKSPACE_ID` /
  `PBI_PILOT_DATASET_ID`.

---

## Verificación

```
1629 passed, 4 skipped      # los 4 requieren el binario de Windows
ruff check / ruff format    # limpio
mypy --strict               # 182 archivos, sin issues
```
