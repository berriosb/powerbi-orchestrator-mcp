# Release Notes — v1.20.0

**Fecha:** 2026-10-20
**Tipo:** Minor — cierres de los tiers de auditoría

> Doce work units, cuatro tiers. Ver "BREAKING CHANGES" más abajo.

La auditoría posterior a la v1.19.0 dejó una lista de quince hallazgos
verificados. Este release los cierra todos, por tiers: primero los que
bloqueaban el uso real, luego los de seguridad, luego los de
corrección y por último los de proceso.

---

## ⚠️ BREAKING CHANGES

### 1. El gate de calidad no pasa sin evidencia

`PreDeployGate` decidía que un artefacto era seguro de publicar sin
mirar nada. Dos caminos:

- Sin findings, todos los contadores eran cero y el gate pasaba. Un
  despliegue a un workspace de producción podía limpiar su gate de
  calidad sin que se ejecutara **ningún** check, y el resultado se leía
  como "se revisó todo y no se encontró nada".
- Una severidad que el perfil no conoce se descartaba.
  `severity: "critical"` no es un nivel que el gate entienda, pero
  desde luego no es un "info", y dejaba pasar el gate en todos los
  perfiles, incluido el estricto.

Ahora el gate falla cerrado en ambos casos: sin findings se reporta
"no hay evidencia para decidir", y una severidad que no puede
interpretar bloquea. Las severidades se normalizan (mayúsculas y
espacios) y el resultado dice cuántos findings se evaluaron y cuáles
severidades no se reconocieron.

Un despliegue bloqueado sigue listando las operaciones que **habría**
realizado, para que un dry run siga siendo informativo.

### 2. El plan de deploy se puede construir

`plan_change(intent="deploy")` no funcionaba. Dos bugs superpuestos:

- `_build_plan_from_intent` inyectaba un argumento `target` en todos
  los templates, pero `build_deploy` toma `pbip_path` y
  `build_dax_regression` un `baseline_path`. Pasarles un `target`
  inesperado levantaba un TypeError antes de llegar a su propia
  validación.
- El plan de deploy se rechaza por defecto, porque su risk score (0.7)
  está por encima del umbral de impacto por defecto (0.5). Ese rechazo
  es correcto — publicar a un workspace, programar un refresh y
  dispararlo es de alto impacto — pero el mensaje solo decía que se
  superaba el umbral. Ahora dice qué opción subir y cuánto, y se
  devuelve como error de plan estructurado en vez de escapar como
  excepción opaca.

### 3. `Tools.Admin` significa algo

El tier administrativo estaba documentado pero nunca se exigía: el
conjunto de tools de administración estaba vacío. Liberar artefactos
entre etapas de un deployment pipeline y aplicar sensitivity labels son
operaciones de gobernanza — cambian lo que un workspace publica y cómo
se clasifican sus datos — así que ahora requieren el tier
administrativo en lugar de simple acceso de escritura.

`Tools.Admin` sigue concediendo todos los niveles inferiores, así que
un cliente que lo tiene sigue funcionando.

### 4. Doce tools sin check de scope

`connect_target`, `plan_change`, `audit_model_and_report`,
`execute_dax_query`, `diff_models`, `pre_deploy_check`,
`generate_data_dictionary`, `select_visuals_for_kpis`,
`optimize_report_performance`, `audit_report_ux_and_storytelling`,
`screenshot_report_pages` y `powerbi_health` no comprobaban ningún
scope: se ejecutaban para quien el transporte dejara pasar. Ahora cada
tool declara el scope que necesita, y un test falla si se deja alguna
fuera.

### 5. El path de lectura está contenido

El path de lectura no tenía contención salvo que se definiera
`PBI_WORKSPACE_ROOT`, así que cualquier directorio fuera de la lista
negra podía leerse — y leer es cómo el contenido llega al contexto del
modelo. Ahora sigue la misma regla que el de escritura: acotado por
`PBI_WORKSPACE_ROOT`, o por el directorio de trabajo si no está
definido. `PBI_ALLOW_OUTSIDE_WORKSPACE_READ=1` es la salida
explícita. **El comportamiento por defecto cambia**, así que quien
dependiera de leer fuera del root tiene que activar la variable.

---

## 🔒 Security

- **El plan de deploy no puede construirse** (ver arriba).
- **Inyección TMDL en `setup_rls_and_roles`.** El nombre del rol y el
  de la tabla se interpolaban en crudo; un nombre con un salto de
  línea cerraba el bloque de rol y abría otro, así que se podían
  escribir roles que la spec no pedía. Ahora se validan y se
  entrecomillan cuando no son identificadores TMDL válidos — lo que
  además arregla nombres como "Region-West", que antes se escribían
  como TMDL inválido.
- **Borrar un plan está acotado a su dueño.** Leerlo ya lo estaba;
  borrarlo no, así que cualquiera que supiera el `plan_id` podía
  borrarlo.
- **La cadena del audit detecta lo que debe detectar.** Los campos
  hasheados se concatenaban, así que mover un carácter de un campo al
  siguiente producía una fila que verificaba limpia; ahora se
  codifican como un array JSON canónico. El anchor — el sidecar que
  registra hasta dónde debe llegar la cadena — no estaba firmado, así
  que truncar la base y reescribir el anchor pasaba desapercibido;
  ahora está firmado con la clave de la cadena. La clave generada
  cuando no hay secreto configurado vive en el home del orquestador y
  **no** junto a la base de datos: el directorio de audit es lo que se
  comparte y se respalda, y una clave al lado de lo que protege no
  protege nada.
- **Identificadores validados en las rutas REST de Fabric** (ya en
  v1.19.0).

---

## ✨ Changed

- **Los motores reales están registrados.** Los adaptadores de
  `powerbi-modeling-mcp`, Tabular Editor y `superbi-mcp` estaban
  definidos pero nunca se instanciaban en `src/`: el selector quedaba
  siempre vacío y cada plan degradaba en silencio al adaptador
  interno. Ahora se construyen los que están instalados, la cadena
  termina en el adaptador interno (que siempre funciona sobre los
  ficheros en disco) y **cada paso dice qué motor lo sirvió**,
  marcando el interno como ruta degradada.
- **Los motores dicen lo que hicieron.** Las cuatro escrituras del
  adaptador de modeling tenían `success=True` fijo. El veredicto del
  motor ahora se respeta. `superbi` tenía el mismo problema y
  additionally trataba como *válido* un informe sin veredicto — un
  informe que nadie había revisado se reportaba como limpio. Su
  fallback al motor interno ahora es solo para el caso documentado
  (el motor no ofrece el método), que el SDK reporta como error
  distinguible: un crash, un timeout o un error de contrato ya no se
  esconden detrás de una escritura "exitosa".
- **El modo skeleton de Tabular Editor es opt-in.** Por defecto
  devolvía la ruta que habría escrito sin escribir nada. Los
  resultados simulados se marcan como tal.
- **Las reglas del linter de DAX disparan donde deben.**
  `BP_SUMMARIZE_FOR_AGG` tenía la alternancia fuera del grupo de
  paréntesis, así que cualquier `AVERAGE(` del expresión se reportaba
  como un problema de SUMMARIZE. `BP_DIVIDE_VS_SLASH` estaba anclada
  al final de línea, así que una división en medio de una expresión
  —justo lo que la regla existe para cazar— pasaba desapercibida. Un
  `//` de comentario ya no cuenta como división.
- **`BP_FILTER_ISFILTERED` mira `ISFILTERED`.** La regla es sobre un
  filtro que no lo está, y nunca lo miraba.
- **El renombrado de columnas actualiza las referencias que un PBIR
  real tiene.** Eran un reemplazo literal de `Table[Column]`, pero un
  PBIR guarda la referencia como objeto estructurado:
  `{"Column": {"Expression": {"SourceRef": {"Entity": "FactSales"}},
  "Property": "TotalAmount"}}`. El texto no aparece, así que el
  renombrado no encontraba nada y el paso reportaba éxito sin haber
  cambiado una sola referencia. El resultado dice además cuántas
  referencias se reescribieron.
- **El alt text se lee donde PBIR lo guarda** (`properties.expr`), y
  el fallback por fin tiene test: el fixture tenía `objects` vacío.
- **Una regresión visual que no se puede medir ya no es "sin
  cambios".** El comparador de Variance decía "unchanged" para dos
  imágenes que no sabía decodificar, aunque sus hashes distintos.
  Ahora esa página es "unknown" y se cuenta aparte.
- **El nombre de página se valida** antes de usarlo como directorio:
  un nombre vacío escribía una página fuera del layout esperado, y un
  nombre con separador escribía fuera del directorio del informe.
- **Los códigos de salida del BPA se mapean.** Todo no-cero era un
  "crash", así que un fallo de autenticación y un fallo de validación
  se confundían. Su timeout apuntaba a
  `PBI_ENGINE_TIMEOUT_TE_S`, una variable que el runner nunca leía:
  ahora el timeout sale de la configuración del motor.
- **Una ejecución viva ya no se marca como huérfana.** El cutoff era
  de 60 segundos, más corto que el paso legítimo más largo.
- **El label del snapshot cumple el contrato del proyecto.** Se
  construía desde el plan id sin normalizarlo, así que violaba el
  patrón de identificadores del propio proyecto — y si lo violaba
  dependía del sufijo aleatorio, que es lo que hacía el fallo
  intermitente.

---

## 🧹 Process

- **El health check distingue los motores que se despachan de los que
  solo se detectan.** Sugerir una instalación que no habilita ninguna
  función manda a nadie en la dirección correcta. `dscmd` y
  `pbip-validator` no tienen adaptador: `pbip-validator` ni siquiera
  es un paquete en PyPI.
- **El E2E nightly puede correr de verdad**: el job pedía un asset que
  no existe (`dscmd-win-x64.zip`) y el zip portable de DAX Studio no
  incluye `dscmd.exe`. Como nada lo usa, la descarga desaparece.

---

## Verification

1350 tests pasando (desde 1240), 110 nuevos. `ruff check`,
`ruff format --check` y `mypy --strict` limpios; cobertura 88 %.

Cada arreglo tiene un test de regresión. El del recorte de sesiones
HTTP se verificó desactivando el arreglo: el token ajeno recuperaba la
sesión y ejecutaba la tool como el propietario.

La suite se ejecutó tres veces seguidas: el label del snapshot era un
fallo intermitente porque dependía del sufijo aleatorio, y ahora está
normalizado y tiene un test para cada forma de sufijo.
