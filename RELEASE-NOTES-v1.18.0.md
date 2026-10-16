# Release Notes — v1.18.0

**Fecha:** 2026-10-16
**Tipo:** Minor — cierre de los 4 hallazgos restantes de la auditoría

> Tres cambios incompatibles. Ver "BREAKING CHANGES" más abajo.

Cierra los cuatro pendientes que quedaron abiertos tras v1.17.0. Los tres
primeros importan sobre todo en despliegues compartidos, donde varios
principales usan el mismo proceso.

---

## ⚠️ BREAKING CHANGES

### 1. Los plugins requieren `PBI_ENABLE_PLUGINS=1`

Cargar un plugin ejecutaba `importlib.exec_module` sobre cualquier `.py` del
directorio de plugins. Con la carga activa por defecto, cualquier usuario
local que pudiera escribir ahí obtenía **ejecución de código dentro del
servidor MCP**, sin ninguna señal para el operador.

No existe una PKI de firma que reemplazar esto de forma equivalente, así que
el control es *opt-in* más higiene de directorio:

- La carga requiere `PBI_ENABLE_PLUGINS=1`.
- Con la variable activa, se rechaza un directorio escribible por grupo o
  por otros, y se rechazan archivos de plugin que sean symlinks.
- `powerbi_health` agrega un check `plugins`, para que el estado sea visible
  en vez de silencioso.

```bash
export PBI_ENABLE_PLUGINS=1
chmod 700 ~/.powerbi-orchestrator-mcp/plugins
```

### 2. `plans.db` gana una columna `owner`

Las filas existentes se migran a propietario `local`. Sobre transporte HTTP,
un plan solo es visible para el principal que lo creó: conocer el `plan_id`
ya no alcanza para ejecutarlo.

### 3. La variable con typo deja de leerse

`PBI_ORCHestrATOR_AUDIT_SECRET` (con minúscula en `ORCHestrator`) ya no se
acepta. Usar `PBI_ORCHESTRATOR_AUDIT_SECRET`.

> En Windows los nombres de variable no distinguen mayúsculas, así que allí
> ambas grafías son la misma variable y este cambio solo afecta a POSIX.

---

## Correcciones de seguridad

### Propiedad de planes (IDOR)

`plans.db` no tenía columna de propietario y `get()` filtraba solo por
`plan_id`. Cualquier principal autenticado que aprendiera o adivinara un
identificador podía ejecutar el plan de otro tenant.

- Nueva columna `owner`, migrada en sitio con las filas previas como `local`.
- `get`, `list_all` y `count` filtran por la identidad del llamador.
- `apply_plan` filtra tanto la caché en memoria como el store.
- Un plan inexistente y uno ajeno devuelven el mismo mensaje, para no
  revelar la existencia del recurso.
- Los llamadores sin autenticación sobre stdio siguen viendo los planes
  locales, con lo que no cambia el comportamiento single-user.

La identidad se toma de los claims de Entra (`oid`, `sub`, `upn`,
`preferred_username`, `appid`, `unique_name`, `name`).

### Symlinks al leer el repositorio

`_list_pbip_files` confía en el listado del árbol, así que un `.pbip`
versionado en un repositorio de terceros podía ser un symlink a cualquier
archivo del host, cuyo contenido terminaba desplegado en Fabric. Ahora se
omite todo lo que sea symlink o resuelva fuera del repositorio, y se
reporta como warning en vez de fallar en silencio.

### Variable de entorno con typo

`_get_hmac_key()` aceptaba la grafía mal escrita como fallback. Una
instalación configurada con el typo parecía configurada cuando la clave que
esperaba nunca se usaba. Ahora solo se honra
`PBI_ORCHESTRATOR_AUDIT_SECRET`, y si no está se genera un archivo de clave.

---

## Corrección de un bug introducido en esta tanda

La primera implementación de `directory_is_private()` usaba los bits de
permiso POSIX de `st_mode`. En Windows esos valores son fijos (`0o666`,
`0o777`) y no.Portan información, de modo que **todo directorio parecía
escribible por otros y la carga de plugins quedaba imposible en esa
plataforma**, rompiendo una ruta que antes funcionaba.

El chequeo se aplica ahora solo donde los bits significan algo
(`os.name == "posix"`); en el resto, el control es la variable de activación.
Hay un test que fija este comportamiento.

---

## Documentación

- `SECURITY.md`: el modelo de amenazas incorpora propiedad de planes, rechazo
  de symlinks y plugins opt-in. La sección "Not implemented" mantiene
  explícito lo que sigue sin cubrir.
- `README.md`: la sección de defaults de seguridad suma plan con dueño y
  plugins desactivados.
- `RELEASE-NOTES-v1.17.0.md`: sus cuatro pendientes quedan tachados y se
  sustituyen por el riesgo residual real.

## Verificación

- `pytest` — **1158 pasaron** (1130 antes de este trabajo), +28 en
  `test_remaining_findings.py`
- `ruff check` + `ruff format --check` — limpios (138 archivos)
- `mypy src tests` con **mypy 2.4.0**, la misma versión que CI — sin errores
  (137 archivos)
- `scripts/verify_mcp_server.py` — handshake MCP real, 28 tools
- CI: 18 checks en verde, incluidos los tres jobs de Windows

Comprobado además de forma manual, no solo por tests:

- Un plan creado por `alice` lo lee `alice`; no lo leen `mallory` ni una
  llamada sin identidad.
- Plugins presentes sin `PBI_ENABLE_PLUGINS`: 0 cargados y el motivo
  reportado por `powerbi_health`.
- Directorio de plugins en `0o700` carga; en `0o777` se rechaza.
- Simulando plataforma no-POSIX, la carga sigue funcionando.

## Riesgo residual

Se mantiene documentado en `SECURITY.md` en lugar de oculto:

- La propiedad de planes se apoya en el SQLite local y en los claims
  presentados al crear el plan; **no revalida** que sigan vigentes.
- No hay verificación de firma de código: un plugin que el operador habilita
  ejecuta con acceso completo al intérprete.
- La cadena HMAC del audit log es *tamper-evident*, no *tamper-proof*: un
  atacante con escritura en el directorio de auditoría puede reescribir la
  base y el ancla.