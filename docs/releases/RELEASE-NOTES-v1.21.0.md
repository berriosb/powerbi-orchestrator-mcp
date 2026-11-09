# Release Notes — v1.21.0

**Fecha:** 2026-10-20
**Tipo:** Minor — refactoring del modelo semántico + cierre de la deuda pendiente

> De 28 a 31 tools. El hilo que recorre todo el release: **una
> herramienta que dice que validó algo tiene que haberlo validado**, y
> una herramienta que propone un cambio tiene que decir **hasta dónde
> puede garantizarlo**.

---

## ✨ Added — tres tools de refactoring del modelo

Las tres son **herramientas de análisis**: reportan lo que han
encontrado y no escriben nada. Se pueden probar contra un modelo real
sin un plan de rollback detrás.

### `simplify_dax_measure` — simplificar

Reescribe una expresión DAX en su forma equivalente y corta, y dice
qué reescrituras la produjeron. Funciona sobre una expresión, sobre
una medida, o sobre todas las medidas de un proyecto.

| Original | Reescrito | Por qué |
|---|---|---|
| `IF(ISBLANK(x), 0, x)` | `COALESCE(x, 0)` | Ambas dan 0 para un blank y el valor si no. |
| `IF(ISERROR(e), fallback, e)` | `IFERROR(e, fallback)` | Mismo resultado, y `e` se evalúa una vez en vez de dos. |
| `IF(c, TRUE(), FALSE())` | `c` | Un `IF` sobre dos booleanos devuelve la condición. |
| `IF(c, a, a)` | `a` | Las dos ramas son iguales, la condición da igual. |
| `IF(ISBLANK(x), BLANK(), x)` | `x` | Un blank entra, un blank sale. |
| `IF(a = 1, x, IF(a = 2, y, z))` | `SWITCH(TRUE(), a = 1, x, a = 2, y, z)` | Una cadena de tests sobre la misma expresión. |
| `x AND TRUE()` / `x OR FALSE()` | `x` | Una tautología a un lado. |
| `NOT(NOT(x))` | `x` | Doble negación. |
| `a + a` | `VAR _a = a RETURN _a + _a` | La misma expresión, calculada una vez. |

La última tiene un matiz, y por eso es la que más cuidado lleva: las
apariciones dentro de un modificador de `CALCULATE` se dejan como
están, porque ahí el mismo texto puede significar otra cosa según el
contexto de filtro, y subirlo cambiaría el resultado.

**La equivalencia es estructural.** La expresión no se evalúa nunca
contra datos, así que el resultado dice `equivalence: "structural"` en
lugar de reclamar una equivalencia que no puede demostrar. Una
expresión que el parser no entiende vuelve sin tocar, con el motivo.

### `abstract_measures` — abstraer

Encuentra la lógica que está escrita más de una vez entre las medidas
de un modelo, y dice **dónde difieren exactamente**.

El análisis va sobre la *forma* de la expresión, no sobre su texto: un
literal y una referencia a columna se sustituyen por marcadores, así
que dos medidas que solo se diferencian en el año o en la tasa salen
con la misma forma, y la diferencia se reporta ranura por ranura.

Dos clases de duplicación:

- **`identical`** — las expresiones son la misma. Una medida puede
  servir para las dos, y hay que repuntar las referencias.
- **`same_shape`** — solo difieren en literales o columnas. Es el caso
  para el que existe un calculation group, y la tool lo dice.

**No aplica nada**, y no es una disculpa: una measure de Tabular **no
puede recibir parámetros**, así que "una measure con un argumento" no
se puede escribir como expresión DAX. La respuesta es un calculation
group, una tabla auxiliar o reescribir las medidas en su sitio, y
cuál de las tres es la correcta depende del modelo.

### `abbreviate_names` — abreviar

Propone nombres más cortos para las medidas y columnas que se han
hecho demasiado largos, y devuelve un mapa de renombrado listo para
`plan_change(intent="safe_rename")`.

La propuesta es mecánica y reversible: una palabra que tiene
abreviatura estándar se acorta (`Amount` → `Amt`, `Quantity` → `Qty`)
y una palabra que no significa nada se va (`of`, `the`, `in`).

Dos cosas que **no** hace, las dos porque un nombre que miente es
peor que un nombre largo:

- **No trunca.** Un nombre que no se puede acortar sin cambiar lo que
  dice se reporta como *necesita un nombre a mano*, porque
  `Total Revenue Amt Incl Tax 2` dice menos que el original y parece
  un error.
- **No propone un nombre que choque** con uno que ya existe. Si choca,
  lo ajusta y lo dice.

---

## 🔧 Added — un parser de DAX

El orquestrador solo miraba una measure con expresiones regulares.
Eso basta para buscar texto, pero no para responder a las preguntas
que necesitan las tools de refactoring: ¿este `IF` está anidado dentro
de otro `IF` sobre el mismo test?, ¿esta subexpresión aparece dos
veces?

Ahora hay un tokenizador y un parser descendente recursivo para el
subconjunto de DAX que usan las medidas: literales, referencias a
columna (con y sin tabla), llamadas, operadores, `AND`/`OR`, bloques
`VAR`/`RETURN`, constructores de tabla y comentarios.

Lo que **no** soporta importa más: una construcción que no entiende
lanza `DaxParseError` con el offset, para que quien llame la deje
intacta en vez de reescribir algo que no pudo leer.

---

## 🔒 Security

- **El audit log se escribe por adelantado.** El intento se registra
  *antes* de que la tool corra y el resultado después. Una mutación
  que muere a medio camino deja un registro de lo que se estaba
  intentando, en vez de nada. La fila del intento lleva el estado
  `attempted`.
- **El audit log puede ser una precondición.**
  `PBI_AUDIT_FAIL_CLOSED=1` rechaza una llamada que no pudo dejar
  constancia, antes de que toque nada. Sigue siendo *fail-open* por
  defecto a propósito: un disco lleno llevaría por delante todas las
  mutaciones.
- **La caché de planes en memoria tiene tope.** Guardaba todos los
  planes que el servidor había hecho, cada uno con su YAML y su lista
  de pasos, durante toda la vida del proceso. El plan store es la
  fuente de verdad, así que la caché puede —y ahora— expulsar el plan
  menos usado.

---

## ✨ Changed

- **El README va seis releases atrasado.** Decía *"Estado actual
  (v1.14.2)"*, afirmaba que el backlog de hardening estaba cerrado
  sin nada pendiente, apuntaba a las release notes de la v1.14.2, y
  pedía instalar `pbip-validator` — un paquete que no existe en PyPI
  — y DAX Studio, que ninguna tool usa.
- **El README enseña los tres flujos completos**: el safe rename, el
  deploy detrás del gate de calidad y el bucle auditar-arreglar-auditar,
  con los argumentos reales. Escribir esas llamadas a mano destapó que
  tres de los parámetros que iba a documentar no existían: un test
  ahora parsea las llamadas del README y comprueba cada argumento
  contra el esquema que publica el servidor.
- **`dscmd` y `pbip-validator` fuera del detector.** Figuraban como
  motores y tenían entrada en la tabla de timeouts, pero ninguna tool
  los alcanza: el health check ofrecía instalarlos y eso no habilitaba
  ninguna función. `pbip-validator` nunca llegó a publicarse en PyPI.
- **El preflight en vivo puede correr programado.** Sigue sin correr
  en push ni en pull requests, así que no puede convertirse en un
  status check obligatorio — esa es la propiedad que el test de guard
  comprueba ahora, en vez del "solo manual" de antes.
- **El Dockerfile instala desde el lock file.** Las dependencias
  estaban escritas a mano y el `uv` pinneado era demasiado viejo para
  leer el lock. El lock tampoco estaba en git: se había quedado en la
  versión 1.18.0 del proyecto, dos releases atrás, sin que nadie lo
  notara porque nada lo leía. Ahora está trackeado, el build usa
  `uv sync --locked` y CI comprueba que el lock está al día.
- **La imagen estaba rota.** `uv sync` instala el proyecto en modo
  editable y el runtime no llevaba el código fuente al que apuntaba.
  Ahora instala una copia real.

---

## Verification

1509 tests pasando (desde 1392), 117 nuevos. `ruff check`,
`ruff format --check` y `mypy --strict` limpios; cobertura 89 %;
`pip-audit` sin CVEs; smoke MCP real con las 31 tools.

Cada arreglo tiene su test de regresión. El del hoisting a variable
comprobó que las apariciones dentro de un modificador de `CALCULATE`
se quedan donde están: subir una expresión que aparece dos veces pero
en contextos de filtro distintos cambiaría el resultado.

La equivalencia de las reescrituras es estructural y así se declara.
Ninguna de ellas se evaluó contra un conjunto de datos, y el resultado
no finge lo contrario.
