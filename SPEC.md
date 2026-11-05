# Advanced Code Review – Especificación

Framework de revisión estática de repositorios para hackathons y concursos de programación.

Versión: 2026-11-04

## Propósito y alcance

El framework revisa de forma estática los repositorios de los equipos participantes con herramientas open source y sin costo adicional, sin importar el lenguaje en que estén programados.

**Criterios que cubre:**

| Criterio | Qué se evalúa |
| --- | --- |
| Factibilidad técnica | Lenguajes, tamaño, arquitectura, dependencias vulnerables o abandonadas |
| Calidad del código | Hallazgos de linters, mantenibilidad |
| Seguridad | Código inseguro (CWE / OWASP Top 10), dependencias con CVE, secretos expuestos |
| Integración responsable de IA | Secretos expuestos, datos enviados al proveedor de IA, filtros, manejo de errores, supervisión humana |
| Plagio | Código copiado de proyectos públicos, similitud entre equipos, ideas o proyectos ya existentes en la web |

**Fuera de alcance:**

- Análisis dinámico (ejecutar la app, pruebas de *prompt injection*, accesibilidad de la interfaz): se cubre en un framework aparte.
- Evaluación del problema, impacto y pitch: la hacen los otros jueces.

## Arquitectura y flujo

Cada repositorio se clona una vez en solo lectura y cada herramienta lo lee una sola vez. Solo MegaLinter y el dashboard usan contenedores.

1. **Leer la entrada** (`equipos.yml`, editable a mano o desde la página Configuración) y **obtener** cada repositorio en `repos/<id>` (solo lectura): clonar URL git, copiar carpeta o descomprimir ZIP. Ver [Entrada](#entrada).
2. **Reconocer** con scc: lenguajes, líneas y archivos por repositorio.
3. **MegaLinter** (1 contenedor por repositorio): calidad, Semgrep, Betterleaks y Trivy en una sola pasada. Envía resultados a Grafana y guarda SARIF/Markdown en `reports/<equipo>/`.
4. **SCANOSS** por repositorio: coincidencias con código open source.
5. **Dolos** una sola vez sobre todos los repositorios: similitud entre equipos.
6. **Claude Code** lee `reports/<equipo>/` (sin volver a escanear), revisa el código y hace la búsqueda de plagio en la web.
7. **Exportar** los resultados de scc, SCANOSS, Dolos y Claude Code como JSON/CSV en `reports/` (ver [Formato de resultados](#formato-de-resultados)).
8. **Generar exportaciones**: reporte Markdown y PDF por equipo, y un ZIP con todos (ver [Exportación de reportes](#exportación-de-reportes)).
9. **Consultar** el dashboard local en Grafana (Prometheus, Loki y archivos vía Infinity) y **compartir** las exportaciones.

| Corre como | Componentes |
| --- | --- |
| Contenedor de un solo uso | MegaLinter |
| CLI directo | scc, SCANOSS, Dolos, md-to-pdf |
| Servicio local (`localhost`) | Grafana (con plugin Infinity), Loki, Prometheus, servidor de archivos de `reports/`, orquestador (API + página Configuración) |
| Asistente | Claude Code |

Todo corre en el equipo del revisor; nada se publica en la web.

## Entrada

La entrada del sistema es un archivo `equipos.yml` con los datos del evento y la lista de equipos. Se edita a mano o desde la página **Configuración** del dashboard; ambos modifican el mismo archivo.

```yaml
evento:
  nombre: "Hackathon 2026"           # aparece en el dashboard y los reportes
  inicio: 2026-11-01T09:00:00-06:00  # detecta commits previos al evento (plagio)
  fin:    2026-11-03T18:00:00-06:00  # detecta commits posteriores al cierre

equipos:
  - id: equipo-01                    # carpeta en repos/ y reports/, y filtro del dashboard
    nombre: "Equipo Alfa"
    proyecto: "Título del proyecto"
    objetivo: "Problema que resuelve y para quién"   # alimenta la búsqueda de plagio web
    repo: https://github.com/org/proyecto-alfa       # URL git
    rama: main                       # opcional, por defecto la rama principal
    commit: a1b2c3d                  # opcional: congela la versión entregada

  - id: equipo-02
    nombre: "Equipo Beta"
    proyecto: "…"
    objetivo: "…"
    repo: C:/entregas/beta.zip       # también carpeta local o ZIP
```

| Campo | Obligatorio | Uso |
| --- | --- | --- |
| `evento.nombre` | Sí | Título en dashboard y reportes |
| `evento.inicio`, `evento.fin` | Sí | Señales de git: commits antes del inicio o después del cierre |
| `equipos[].id` | Sí | Identificador único; solo minúsculas, números y guiones |
| `equipos[].nombre`, `proyecto` | Sí | Etiquetas en dashboard y reportes |
| `equipos[].objetivo` | Sí | Palabras clave para la búsqueda de plagio en la web |
| `equipos[].repo` | Sí | URL git, carpeta local o archivo ZIP |
| `equipos[].rama`, `commit` | No | Fijan la versión revisada; sin `commit` se toma el último de la rama |

**Reglas:**

- `equipos.yml` contiene datos de los equipos: va en `.gitignore`. El repositorio solo incluye `equipos.example.yml`.
- Para repositorios privados se usan las credenciales de git del revisor; nunca se guardan tokens en `equipos.yml`.
- Si un `id` ya fue analizado y cambia su `repo` o `commit`, se vuelve a obtener y analizar solo ese equipo.

### Página Configuración (desde el dashboard)

Grafana es de solo lectura y el plugin de formularios (Business Forms) figura como de pago en el catálogo de Grafana, así que la configuración vive en un **orquestador** propio: un servicio local ligero (por ejemplo, Python + FastAPI) con una API y una página web. El navbar enlaza a ella con el botón **Configuración**.

| Sección de la página | Qué permite |
| --- | --- |
| Evento | Editar nombre, inicio y fin |
| Equipos | Tabla con alta, edición y baja de equipos (todos los campos de `equipos.yml`) |
| Subir ZIP | Cargar la entrega de un equipo como ZIP; se guarda en `entregas/<id>.zip` |
| Validar | Comprobar cada repo antes de analizar: URL accesible, rama y commit existen, ZIP válido |
| Analizar | Botón por equipo y botón "Analizar todos"; lanza el pipeline completo |
| Estado | Por equipo: pendiente / obteniendo / analizando / listo / error, hora de la última corrida y enlace al log |

**API del orquestador:**

| Método y ruta | Acción |
| --- | --- |
| `GET /api/evento`, `PUT /api/evento` | Leer y guardar los datos del evento |
| `GET /api/equipos` | Listar equipos |
| `POST /api/equipos`, `PUT /api/equipos/{id}`, `DELETE /api/equipos/{id}` | Alta, edición y baja (escriben `equipos.yml`) |
| `POST /api/equipos/{id}/zip` | Subir la entrega ZIP de un equipo |
| `POST /api/equipos/{id}/validar` | Validar el origen del repo |
| `POST /api/analisis` (todos) o `POST /api/analisis/{id}` | Lanzar el análisis |
| `GET /api/analisis/estado` | Estado por equipo (también lo lee Grafana vía Infinity) |

**Seguridad del orquestador:**

- Escucha solo en `localhost`; sin acceso externo.
- Valida `id` (sin rutas ni caracteres especiales) y el origen del repo antes de usarlo.
- Descomprime ZIP con protección contra rutas maliciosas (*zip slip*) y límite de tamaño.
- Nunca ejecuta el código de los equipos: solo lo obtiene y lo pasa a las herramientas de análisis.
- Un análisis a la vez por equipo; un segundo clic mientras corre no lanza otro.

## Stack de componentes

| # | Paso | Herramienta | Cómo corre | Alcance | Criterio | Destino | Costo |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Reconocimiento de lenguaje y tamaño | [scc](https://github.com/boyter/scc) | CLI | Por repositorio | Factibilidad | Grafana (Infinity) | Gratis |
| 2 | Calidad de código | [MegaLinter](https://megalinter.io/latest/supported-linters) (linters del lenguaje detectado) | 1 contenedor | Por repositorio | Calidad | Grafana | Gratis |
| 3 | Seguridad del código (CWE / OWASP Top 10) | Semgrep (en MegaLinter) | Mismo contenedor | Por repositorio | Seguridad | Grafana | Gratis |
| 4 | Secretos expuestos | Betterleaks (en MegaLinter) | Mismo contenedor | Por repositorio, con historial git | Seguridad, IA responsable | Grafana | Gratis |
| 5 | Dependencias vulnerables (CVE + CVSS) | Trivy (en MegaLinter) | Mismo contenedor | Por repositorio | Seguridad, factibilidad | Grafana | Gratis |
| 6 | Plagio de código externo | [SCANOSS](https://osskb.org/) (osskb.org) | CLI | Por repositorio | Plagio | Grafana (Infinity) | Gratis, con límite de uso |
| 7 | Plagio entre equipos | Dolos | CLI | Una vez, todos los repositorios juntos | Plagio | Grafana (Infinity) | Gratis |
| 8 | Revisión asistida | Claude Code (`/code-review`, `/security-review`) | Lee reportes 1–7 | Por repositorio | Factibilidad, calidad, IA responsable | Grafana (Infinity) + reporte Markdown | Suscripción existente |
| 9 | Plagio de ideas en la web | Claude Code + búsqueda web | Búsqueda por título, objetivo y palabras clave | Por proyecto | Plagio | Grafana (Infinity) | Suscripción existente |
| — | Dashboard local | Grafana + Loki + Prometheus + [Infinity](https://grafana.com/docs/plugins/yesoreyeram-infinity-datasource/latest/) (Apache 2.0) | Servicio local | Todos los equipos | Consulta del revisor | Revisor | Gratis |
| — | Exportación de reportes | [md-to-pdf](https://github.com/simonhaenisch/md-to-pdf) (MIT) + ZIP | CLI | Por equipo y global | Presentación | Jueces y organizadores | Gratis |
| — | Entrada y orquestación | Orquestador propio (API + página Configuración) sobre `equipos.yml` | Servicio local | Todos los equipos | Configuración y ejecución | Revisor | Gratis |

## MegaLinter

MegaLinter corre una sola vez por repositorio y cubre los pasos 2 a 5. Las herramientas que se traslapan se desactivan para no analizar lo mismo dos veces.

| Categoría | Activo | Desactivado (duplica) |
| --- | --- | --- |
| Calidad | Linters del lenguaje detectado automáticamente | — |
| Seguridad del código | Semgrep | — |
| Secretos | Betterleaks | Gitleaks (deprecado en v9.6), TruffleHog, Secretlint |
| Dependencias | Trivy | OSV-Scanner, Grype |

**Requisitos de configuración (`.mega-linter.yml`):**

- Versión 10 o superior, para los dashboards de Grafana con "Why this rating?".
- Repositorio montado en solo lectura; reportes escritos fuera del repositorio.
- Salida SARIF y Markdown por repositorio (respaldo si Grafana no está disponible).
- `API_REPORTER: true` y `API_REPORTER_PROVIDER: grafana`, con las URLs de Loki y Prometheus.
- Nombre del repositorio = identificador del equipo, para que el dashboard los distinga.

Fuentes: [MegaLinter – linters de repositorio v9.6](https://megalinter.io/9.6.0/descriptors/repository/), [API Reporter v10](https://megalinter.io/10.0.0/reporters/ApiReporter/).

## Dashboard local

Grafana reúne en 6 páginas, unidas por un navbar, todos los resultados del framework: calidad y seguridad (MegaLinter) y tamaño, plagio y revisión asistida (archivos JSON/CSV). Corre solo en `localhost`; los jueces y organizadores reciben los reportes exportados.

### Componentes

| Componente | Función |
| --- | --- |
| Grafana | Interfaz, dashboards y cuentas de usuario |
| Prometheus | Métricas numéricas de MegaLinter por repositorio y linter |
| Loki | Detalle de hallazgos de MegaLinter: corridas, linters, reglas y archivos |
| Plugin Infinity | Lee los JSON/CSV de scc, SCANOSS, Dolos y Claude Code por URL |
| Servidor de archivos | Sirve `reports/` en solo lectura en `localhost`: datos para Infinity y descargas de los botones de exportación |

### Navegación

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Advanced Code Review                                Equipo: [Todos ▾]    │
│ [Resumen] [Equipo] [Calidad] [Seguridad] [Plagio] [Calificación]         │
│ [Configuración ⚙]        Exportar: [PDF equipo] [MD equipo] [ZIP todos]  │
└──────────────────────────────────────────────────────────────────────────┘
```

- **Configuración:** enlace a la página del orquestador para cargar equipos y lanzar análisis (ver [Página Configuración](#página-configuración-desde-el-dashboard)).

- **Navbar:** *dashboard links* de Grafana; todas las páginas llevan la etiqueta `advanced-code-review` y se muestran como botones arriba.
- **Botones de exportación:** *dashboard links* de tipo enlace que apuntan al servidor de archivos y usan la variable `equipo` en la URL (ver [Exportación de reportes](#exportación-de-reportes)).
- **Variable `equipo`:** selector Todos / uno / varios; se conserva al cambiar de página (*include current template variable values*).
- **Variable `herramienta`:** solo en Calidad y Seguridad, filtra por linter.
- **Clic en un equipo** en cualquier tabla abre la página Equipo filtrada.

Origen de datos: **ML** = MegaLinter (Prometheus/Loki, nativo) · **Archivo** = JSON/CSV vía Infinity.

### 1. Resumen: comparar a todos los equipos

| Sección | Gráfica | Qué muestra | Origen |
| --- | --- | --- | --- |
| Estado del análisis | Tabla con color | Por equipo: pendiente / analizando / listo / error y hora de la última corrida | Orquestador (Infinity) |
| Indicadores generales | 4 tarjetas (Stat) | Equipos revisados · quality gates aprobados · errores bloqueantes · secretos encontrados | ML |
| Calificación por equipo | Tabla con celda de color A–E | Equipo, nota, puntaje 0–100, errores bloqueantes y no bloqueantes | ML |
| Puntaje comparado | Barras horizontales ordenadas | Puntaje de salud por equipo, umbrales en 90/80/65/50 | ML |
| Errores por equipo | Barras apiladas | Errores por categoría: calidad, seguridad, secretos, dependencias | ML |
| Alertas de plagio | Tabla | Equipos con coincidencias en SCANOSS, Dolos o web, y cuántas | Archivo |

### 2. Equipo: ficha de un equipo

| Sección | Gráfica | Qué muestra | Origen |
| --- | --- | --- | --- |
| Ficha | Tarjetas | Nota A–E · quality gate · líneas de código · número de archivos | ML + scc |
| Lenguajes | Barra horizontal | Líneas por lenguaje | Archivo (scc) |
| Estado de herramientas | Tabla con color | Cada linter: OK / advertencia / error, número de errores, duración | ML |
| Archivos con más problemas | Barras top 10 | Archivos con más hallazgos | ML (Loki) |
| Revisión asistida | Tabla | Hallazgos de Claude Code: criterio, gravedad, `archivo:línea`, descripción | Archivo |
| Checklist de IA responsable | Tabla ✓/✗ | Los 8 puntos de la checklist | Archivo |

### 3. Calidad: código y mantenibilidad

| Sección | Gráfica | Qué muestra | Origen |
| --- | --- | --- | --- |
| Errores por linter | Barras agrupadas (equipo × linter) | Errores de cada linter de calidad por equipo | ML |
| Matriz de errores | Tabla matriz coloreada | Filas = equipos, columnas = linters, color = número de errores | ML |
| Reglas más violadas | Barras top 10 | Reglas más incumplidas (todas o por equipo) | ML (Loki) |
| Detalle | Tabla de registros | Salida del linter seleccionado | ML (Loki) |

### 4. Seguridad: código inseguro, secretos y dependencias

| Sección | Gráfica | Qué muestra | Origen |
| --- | --- | --- | --- |
| Indicadores | 4 tarjetas rojo/verde | **CVE críticas/altas con corrección disponible** (indicador principal) · hallazgos de Semgrep · secretos (Betterleaks) · total de CVE | Archivo (SARIF) + ML |
| CVE por severidad | Barras apiladas por equipo | CVE de dependencias por severidad CVSS: crítica, alta, media, baja | Archivo (Trivy SARIF) |
| Dependencias vulnerables | Tabla ordenada por CVSS | CVE, paquete, versión instalada, versión corregida, CVSS, severidad | Archivo (Trivy SARIF) |
| Código inseguro por categoría | Barras por equipo | Hallazgos de Semgrep agrupados por CWE y categoría OWASP Top 10 | Archivo (Semgrep SARIF) |
| Patrones inseguros | Tabla | Regla, CWE, severidad, archivo y línea | Archivo (Semgrep SARIF) |
| Secretos expuestos | Tabla | Equipo, archivo y tipo de secreto, **sin mostrar el valor** | ML (Loki) |

**Uso de CVE y CWE:**

- **CVE** solo aplica a dependencias publicadas (Trivy): el código del equipo es nuevo y no tiene CVE propios.
- **CWE / OWASP Top 10** clasifica las debilidades del código del equipo (Semgrep).
- **Filtro de ruido:** en un hackathon es común tener muchos CVE por versiones viejas sin impacto real. El indicador principal cuenta solo CVE **críticas y altas con versión corregida disponible**, las que el equipo podía evitar actualizando.
- MegaLinter no envía la severidad como métrica, por eso esta página lee el SARIF de Trivy y Semgrep vía Infinity.

### 5. Plagio

| Sección | Gráfica | Qué muestra | Origen |
| --- | --- | --- | --- |
| Indicadores | 3 tarjetas | Coincidencias con código externo · pares de equipos similares · proyectos parecidos en la web | Archivo |
| Similitud entre equipos | Tabla matriz equipo × equipo, color por % | Similitud de Dolos entre cada par | Archivo (Dolos) |
| Código externo | Barras por equipo + tabla | % de archivos con coincidencia; por archivo: proyecto de origen, licencia, enlace | Archivo (SCANOSS) |
| Ideas en la web | Tabla | Equipo, proyecto encontrado, enlace, fecha, nivel de parecido (idea/estructura/texto) | Archivo (Claude Code) |
| Historial de git | Tabla | Commits anteriores al evento, commit único, fork | Archivo |

### 6. Calificación: ¿por qué esta nota?

| Sección | Gráfica | Qué muestra | Origen |
| --- | --- | --- | --- |
| Fórmula | Texto | Puntaje = 100 × (linters sin errores + 0.5 × linters con errores no bloqueantes) / total de linters. A ≥ 90 · B ≥ 80 · C ≥ 65 · D ≥ 50 · E < 50 | Fijo |
| Nota del equipo | Medidor 0–100 con franjas A–E | Puntaje del equipo seleccionado | ML |
| Desglose | Dona | Linters sin errores / con advertencia / con error | ML |
| Qué baja la nota | Barras | Linters que más restan puntos | ML |
| Aviso | Texto | La nota cuenta linters, no errores: un linter con 1 error resta igual que uno con 100. Revisar errores en Calidad | Fijo |

Las páginas 1, 3, 4 y 6 parten de los dashboards de MegaLinter (Org Overview, Linter Detail, Top Rules & Files, Why this rating?), adaptados. La página 5 y las secciones de archivo de la página 2 son nuevas.

### Formato de resultados

Cada herramienta deja su resultado en `reports/` para que Infinity lo lea:

| Archivo | Generado por | Contenido |
| --- | --- | --- |
| `reports/<equipo>/scc.json` | scc | Lenguajes, líneas y archivos |
| `reports/<equipo>/scanoss.json` | SCANOSS | Coincidencias por archivo: proyecto, licencia, enlace |
| `reports/<equipo>/trivy.sarif` | Trivy (MegaLinter) | CVE, paquete, versiones instalada y corregida, CVSS, severidad |
| `reports/<equipo>/semgrep.sarif` | Semgrep (MegaLinter) | Regla, CWE, categoría OWASP, severidad, archivo, línea |
| `reports/dolos/pairs.csv` | Dolos | Similitud por par de equipos |
| `reports/<equipo>/review.json` | Claude Code | Hallazgos: criterio, gravedad, archivo, línea, descripción; checklist de IA |
| `reports/<equipo>/web-plagiarism.json` | Claude Code | Proyectos encontrados: enlace, fecha, nivel de parecido |
| `reports/<equipo>/git-signals.json` | Script de git | Fecha del primer commit, número de commits, si es fork |

Limitaciones de Infinity: no está pensado para volúmenes grandes de datos (suficiente para un hackathon) y lee por URL, por eso `reports/` se sirve con un servidor de archivos local.

### Exportación de reportes

La exportación a PDF integrada de Grafana es de pago, así que el pipeline genera los reportes de antemano y los botones del navbar solo los descargan.

| Botón | Descarga | URL (servidor de archivos) | Se genera con |
| --- | --- | --- | --- |
| PDF equipo | Reporte del equipo seleccionado en PDF | `/reports/${equipo}/reporte.pdf` | md-to-pdf a partir del Markdown |
| MD equipo | Reporte del equipo seleccionado en Markdown | `/reports/${equipo}/reporte.md` | Plantilla de [Entregables](#entregables) |
| ZIP todos | Reportes de todos los equipos + CSV de Dolos | `/reports/todos.zip` | Script al final del pipeline |

- Con `equipo = Todos`, los botones PDF y MD se ocultan o apuntan al ZIP.
- **Datos de una gráfica:** cada panel ya permite *Inspect → Data → Download CSV* en Grafana, sin configuración extra.
- **Captura de una página:** impresión del navegador (Ctrl+P → Guardar como PDF).
- Si se vuelve a analizar un repositorio, las exportaciones se regeneran en el mismo paso.

### Cómo se comparte

- El dashboard no se publica: corre en `localhost`, sin cuentas ni acceso externo.
- Jueces y organizadores reciben el ZIP o los PDF por equipo por el canal que indiquen los organizadores.
- Para mostrarlo en vivo: compartir pantalla en una reunión.

Fuentes: [MegaLinter – dashboards de Grafana](https://megalinter.io/10.0.0/observability/grafana/), [MegaLinter – observabilidad y métricas](https://megalinter.io/10.0.0/observability/), [Infinity data source](https://grafana.com/docs/plugins/yesoreyeram-infinity-datasource/latest/).

## Detección de plagio

El plagio se revisa en tres niveles. Una coincidencia indica similitud, no copia confirmada, y la decisión final es del revisor.

| Nivel | Herramienta | Qué detecta | Entrada |
| --- | --- | --- | --- |
| Código externo | SCANOSS | Archivos o fragmentos copiados de proyectos open source conocidos | Huellas (hashes) de cada archivo; no se envía el código |
| Entre equipos | Dolos | Similitud de código entre los repositorios | Todos los repositorios en una sola corrida |
| Ideas y proyectos | Claude Code + búsqueda web | Proyectos ya existentes con la misma idea, aunque el código esté reescrito | Título, objetivo y palabras clave del README y del código |

**Búsqueda web (nivel 3):**

1. Extraer título, objetivo (problema y usuario) y palabras clave distintivas: frases del README, nombres de funciones poco comunes, comentarios.
2. Buscar en GitHub, Devpost y otras galerías de hackathons, Product Hunt y tutoriales.
3. Comparar idea, estructura y texto; anotar si la fecha del hallazgo es anterior al hackathon.
4. Reportar cada coincidencia con su enlace.

**Señales en el historial de git:** commits anteriores al inicio del hackathon, un solo commit con todo el código, o un repositorio que es fork de otro.

**Antes de marcar plagio:** confirmar con los organizadores si las reglas permiten plantillas, código previo o código generado con IA.

## Revisión asistida con Claude Code

Claude Code cubre lo que las herramientas no miden: arquitectura, factibilidad y cómo integran la IA. Produce un borrador que el revisor valida.

**Entradas:** el repositorio y los reportes de `reports/<equipo>/` (scc, MegaLinter, SCANOSS, Dolos).

**Checklist de integración responsable de IA:**

- [ ] ¿Qué datos del usuario (menores) se envían al proveedor de IA?
- [ ] ¿Se guardan conversaciones o datos personales? ¿Dónde y cuánto tiempo?
- [ ] ¿Las API keys están fuera del código (variables de entorno)?
- [ ] ¿Hay filtros o moderación de entrada y salida?
- [ ] ¿Qué pasa si la IA falla, tarda o responde algo inapropiado?
- [ ] ¿El usuario sabe que interactúa con una IA?
- [ ] ¿Hay supervisión de un adulto (padre, docente)?
- [ ] ¿Los prompts del sistema están en el código y son revisables?

**Riesgos de [OWASP Top 10 para aplicaciones LLM](https://genai.owasp.org/llm-top-10/) visibles en el código** (la prueba real es dinámica y queda fuera de este framework):

- [ ] *Prompt injection*: ¿la entrada del usuario se concatena al prompt del sistema sin separar ni validar?
- [ ] Manejo inseguro de la salida: ¿la respuesta del modelo se ejecuta, se inserta como HTML o se usa en consultas sin validar?
- [ ] Divulgación de información sensible: ¿se envían datos personales o secretos al modelo?
- [ ] Agencia excesiva: ¿el modelo puede ejecutar acciones (herramientas, APIs, archivos) sin confirmación humana?
- [ ] Consumo sin límite: ¿hay límites de tokens, de peticiones o de costo?

**Salida:** sección cualitativa del reporte por equipo, con referencias `archivo:línea` para cada hallazgo.

## Entregables

Jueces y organizadores reciben un reporte por equipo en PDF y Markdown, y un ZIP con todos; el dashboard de Grafana queda como herramienta local del revisor.

**Plantilla del reporte por equipo:**

```markdown
# Reporte de revisión – <Equipo> – <Proyecto>

## Resumen
- Lenguajes y tamaño (scc):
- Enlace al dashboard (Grafana):
- Hallazgos críticos:

## Factibilidad técnica
## Calidad del código
## Seguridad
- CVE críticas/altas con corrección disponible:
- Código inseguro (CWE / OWASP Top 10):
- Secretos expuestos:
## Integración responsable de IA
## Plagio
- Código externo (SCANOSS):
- Entre equipos (Dolos):
- Ideas en la web:
- Historial de git:

## Recomendaciones al equipo
```

## Entorno, seguridad y privacidad

El código es de estudiantes y de terceros: se analiza aislado, sin ejecutarlo y sin publicarlo.

- **Contenedores:** Podman (gratis sin restricciones) o Docker Desktop si el uso es personal o educativo.
- **CLI:** scc, SCANOSS (Python), Dolos (Node.js).
- **Solo lectura:** los repositorios se montan sin permiso de escritura; ninguna herramienta los modifica.
- **No se ejecuta** el código de los equipos en este framework.
- **SCANOSS** recibe solo huellas (hashes), no el código.
- **Secretos encontrados** se reportan a los organizadores; no se publican en el dashboard en texto plano.
- **Al terminar** la revisión se borran los clones locales, salvo que los organizadores indiquen otra cosa.
- **Este repositorio** no debe contener código ni reportes de los equipos (`repos/` y `reports/` van en `.gitignore`).

## Riesgos y pendientes

| Riesgo | Mitigación |
| --- | --- |
| Un equipo usa un lenguaje sin linter en MegaLinter o una herramienta *no-code* | La revisión asistida (paso 8) lo cubre |
| SCANOSS gratuito llega a su límite de consultas | Priorizar archivos propios del equipo; excluir dependencias |
| Dolos no soporta un lenguaje usado | Comparar solo los lenguajes soportados y reportarlo |
| Reportes exportados desactualizados tras re-analizar | Regenerar exportaciones en el mismo paso del pipeline |
| Falsos positivos de las herramientas o de la IA | Toda conclusión la valida el revisor |

**Pendientes con los organizadores:**

- [ ] ¿Cómo se entregan los repositorios (GitHub, invitación, zip)?
- [ ] ¿Las reglas permiten plantillas, código previo o código generado con IA?
- [ ] ¿La detección de plagio es parte del rol de Code Reviewer?
- [ ] ¿Hay rúbrica oficial del evento para alinear los reportes?
- [ ] ¿Por qué canal se entregan los reportes a jueces y organizadores?
- [ ] ¿La seguridad pesa dentro de "calidad del código" o se reporta como criterio aparte?

## Fuentes

- [MegaLinter – linters soportados](https://megalinter.io/latest/supported-linters)
- [MegaLinter – dashboards de Grafana](https://megalinter.io/10.0.0/observability/grafana/)
- [MegaLinter – API Reporter](https://megalinter.io/10.0.0/reporters/ApiReporter/)
- [osskb.org (SCANOSS)](https://osskb.org/)
- [SCANOSS en OpenChain](https://openchain-project.github.io/OpenChain-KWG/en/guide/tools/9-scanoss/)
