# Release Notes — v1.22.0

**Fecha:** 2026-10-21
**Tipo:** Minor — cierre de la contención de lectura + tools que ahora sí funcionan sobre MCP

> Un release que empieza mirando donde nadie miró: **las herramientas
> que el LLM puede invocar por MCP**. Resultado: cuatro tools leían
> una ruta sin acotar, dos de ellas publicaban lo leído a la nube, y dos
> tools no podían hacer su trabajo porque el protocolo no les deja
> recibir un callable de Python.

---

## 🔒 Security — la lectura también estaba acotada, pero no en todas partes

En v1.21.1 se corrigió la **escritura**: tres tools validaban su ruta de
salida como escritura. Lo que no se revisó con el mismo rigor fue la
**lectura**. Y la lectura es lo que lleva contenido al contexto del
modelo.

Cuatro tools leían una ruta que elegía el llamador, sin validarla:

| Tool | Alcance | Qué hacía con la ruta |
|---|---|---|
| `optimize_report_performance` | `Tools.Read` | Leía las páginas PBIR y devolvía hotspots |
| `audit_report_ux_and_storytelling` | `Tools.Read` | Leía las páginas PBIR y devolvía findings |
| `deploy_to_workspace` | `Tools.Write` | Base64 de **cada archivo** y envío a la API de Fabric |
| `sync_git_to_workspace` | `Tools.Write` | Enumera un repo git y publica sus `.pbip` |

Las dos primeras devuelven nombres de página e ids de visual leídos del
disco, así que un llamador con `Tools.Read` podía traer un reporte de
cualquier parte del host al contexto del modelo. Las dos últimas son
peores: `deploy_to_workspace` codificaba en base64 todo lo que encontraba
bajo la ruta y lo mandaba a la API.

**Las cuatro ahora validan la ruta.** Las dos que publican la validan
como **escritura**, a propósito:

> `PBI_ALLOW_OUTSIDE_WORKSPACE_READ=1` existe para dejar leer fuera del
> workspace. Si `deploy_to_workspace` usara el validador de lectura, esa
> bandera ampliaría también lo que se puede **mandar a la nube**. Lo que
> sale del proceso está acotado por el workspace, no por un permiso de
> lectura.

### Verificado, no asumido

Con el root apuntando a un directorio y un reporte plantado **fuera** de
él:

- `optimize_report_performance` lo leía (`pages_analyzed: 1`) y el
  nombre del directorio externo volvía en el resultado como `page_name`.
- Las cuatro peticiones ahora vuelven como rechazo, sobre el protocolo
  MCP real:

```
Error executing tool deploy_to_workspace: path '/tmp/.../outside/report.pbip'
is outside allowed workspace root '/tmp/.../ws'
```

### Una corrección al CHANGELOG de v1.21.1

La nota de v1.21.1 decía que "la contención ahora está en ese único
choke point, así que toda tool que lee un modelo queda cubierta por una
comprobación". **Eso no era cierto.** `gather_pbip_metadata` lo usan
cuatro tools, no todas las que leen un modelo. Queda escrito en el
CHANGELOG de esta versión para que la corrección no sea silenciosa.

---

## 🛠 Fixed — dos tools que no podían hacer su trabajo

`add_measure_with_validation` quiere un `measure_writer`.
`refactor_to_calculation_groups` quiere un `inspector` y un
`measure_writer`. Son **callables de Python**, y un cliente MCP no puede
inyectar uno: sobre el protocolo siempre fueron `None`.

Las dos fallaban **con honestidad** — que era el objetivo del fix del
bug de éxito silencioso — pero no podían hacer su trabajo:

| Tool | Antes | Ahora |
|---|---|---|
| `add_measure_with_validation` | `"no measure_writer available; measure was NOT persisted"` | Crea la medida **en disco** |
| `refactor_to_calculation_groups` | `"no inspector provided — cannot enumerate measures"` | Enumera el modelo y decide |

El servidor construye ambos a partir del motor que el orquestador ya
tiene, por la misma selección que usa el camino de los planes: un motor
real si está instalado, el adaptador built-in si no.

### El `asyncio.run` que no podía funcionar

El tool de refactor llamaba `asyncio.run(...)` sobre la corrutina del
motor. El servidor MCP corre cada tool **dentro de un event loop**, donde
`asyncio.run()` lanza `cannot be called from a running event loop`. Es
decir: aunque el wiring hubiera existido, todo refactor real se habría
convertido en un warning. La corrutina corre ahora en un worker thread
con su propio loop.

### Una medida escrita a medias

El adaptador built-in persistía nombre, tabla y expresión, y **se
perdía** el formato, la descripción y la marca de oculto — mientras la
tool reportaba éxito. Ahora escribe los atributos opcionales también, y
la tool reporta lo que persistió.

---

## 🔧 Fixed — la CI verde que no probaba nada

El job nightly `e2e` salía **verde sin ejecutar los tests del motor**:

1. El binario `te` se descargaba en un job aparte (`engines`), que lo
   escribía en el temp de **su** runner y terminaba. El binario nunca
   llegaba a los tests.
2. Los nueve tests de `te` se saltaban solos (`engine_te_path` no
   encontraba el binario).
3. El guard solo fallaba si se saltaba **todo**, y tres tests de
   validación de PBIP pasaban.

Resultado: el job pasaba sin haber probado nada. Además, el paso
"Report engine availability" no llevaba `shell: bash`, así que en el
runner Windows corría bajo PowerShell — donde `$(command -v te)` no es
sintaxis de shell — e imprimía vacío, haciendo que una descarga que
funcionaba pareciera una descarga fallida.

Ahora la descarga ocurre en el job que corre los tests, y **el guard
falla si los tests de `te` se saltan en el runner que sí tiene el
binario**. Comprobado con JUnit sintético: el caso "3 passed, 9 skipped"
ahora falla; Linux y macOS siguen pasando legítimamente.

---

## 📚 Documentación

- El README decía v1.20.0 y 28 tools; `SECURITY.md` listaba 1.19.x como
  la versión más reciente soportada; `docs/pilot-validation.md` decía
  1158 tests. Todo corregido.
- `SECURITY.md` documenta ahora que las tools que publican a la nube
  validan su ruta como escritura.

---

## Lo que sigue igual, y conviene decirlo otra vez

Este release arregla lo que estaba roto. **No cambia los límites de
deployment**, que siguen siendo:

- **Un solo nodo.** El plan store es un SQLite local; dos réplicas no
  comparten planes, y un volumen NFS compartido no es un workaround
  soportado.
- **Los scope checks fallan open sobre stdio**, que es el modo de
  despliegue por defecto. Sin identidad ligada, las garantías de scope
  no son nada: el control real es quién tiene acceso al proceso.
- **El audit log no registra quién actuó.** Registra *qué*. Atribuir una
  acción a una persona requiere el audit log y el plan store juntos.
- **Nunca se ha validado contra un tenant real.**
  `pilot-validation.yml` sale "success" pero es un *skip*: los secrets
  no existen. Todo el camino cloud — deploy, refresh, promote, RLS,
  DAX — nunca tocó una API real.
- **El `te` real sigue sin probarse.** Los nueve tests son de contrato
  con dobles; el binario de verdad no se ha ejecutado contra un modelo.
  Con este cambio al menos la CI lo dirá si vuelve a saltarse.

---

## Verificación

```
1568 passed, 9 skipped          # +21 tests nuevos
89% cobertura
ruff check / ruff format        # limpio
mypy --strict                   # 178 archivos, sin issues
pip-audit --strict              # 0 CVEs
verify_mcp_server.py            # 31 tools, handshake OK
```
