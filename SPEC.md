# Advanced Code Review – Especificación

Framework de revisión estática de repositorios para hackathons y concursos de programación.

Versión: 2026-11-11

## Propósito y alcance

El framework revisa de forma estática los repositorios de los equipos participantes con herramientas open source y sin costo adicional, sin importar el lenguaje en que estén programados. Su objetivo es reunir evidencia para el **checklist oficial de code review** del evento (ver [Checklist oficial](#checklist-oficial-de-code-review)).

**Criterios evaluados (checklist oficial):**

| Sección | Qué se evalúa |
| --- | --- |
| 1. Desarrollo auténtico | El historial refleja trabajo iterativo y colaborativo durante el reto |
| 2. Código original y con propósito | Estructura, decisiones intencionales, sin reutilización sin explicar, entendido por el equipo |
| 3. Documentación y atribución | README claro, instalación y dependencias, declaración de recursos externos y asistencia de IA |
| 4. Coherencia con la demo, el usuario y seguridad básica | El código coincide con lo demostrado, considera al usuario y no expone secretos ni datos privados |

**Evidencia técnica de apoyo:** las herramientas agrupan sus hallazgos en estas categorías. Alimentan los puntos del checklist; lo que el checklist no pide se reporta en un **anexo técnico** que no puntúa.

| Categoría | Qué se analiza | Uso |
| --- | --- | --- |
| Factibilidad técnica | Lenguajes, tamaño, arquitectura, dependencias vulnerables o abandonadas | Contexto; anexo técnico |
| Calidad del código | Hallazgos de linters, código duplicado, mantenibilidad | Punto 2.1; la nota A–E va al anexo técnico |
| Seguridad | Secretos expuestos; código inseguro (CWE / OWASP Top 10), dependencias con CVE | Secretos: punto 4.3. CWE y CVE: anexo técnico |
| Integración responsable de IA | Datos enviados al proveedor de IA, filtros, manejo de errores, supervisión humana | Anexo técnico |
| Plagio | Código copiado de proyectos públicos, similitud entre equipos, ideas o proyectos ya existentes en la web | Punto 2.4 |

**Fuera de alcance:**

- Análisis dinámico (ejecutar la app, pruebas de *prompt injection*, accesibilidad de la interfaz): se cubre en un framework aparte.
- Evaluación del problema, impacto y pitch: la hacen los jueces con la rúbrica del pitch (10 criterios, hasta 50 puntos).
- Comprobar que los integrantes pueden explicar su código (punto 2.3) y juzgar el diseño y la UX (punto 4.2): requieren entrevista o demo; el reporte los marca como **revisión manual**.
- Determinar si un código fue generado con IA: no hay método confiable. Se revisa que el uso de IA esté declarado y sea coherente con lo encontrado.

## Checklist oficial de code review

Fuente: *Technical Code Review Checklist* (Spark Center for Innovation in Learning, North American AI Challenge), compartido por los organizadores. Aplica a todos los equipos. El documento original no se guarda en este repositorio.

Cada punto se marca como **cumple**, **no cumple** o **revisión manual**, con su evidencia. Al final va el *Reviewer feedback* del equipo.

| # | Punto del checklist | Evidencia | Herramienta | Automatizable |
| --- | --- | --- | --- | --- |
| 1.1 | Commits con iteración significativa, sin cargas masivas sin explicar | Número de commits, tamaño de cada commit, % del código en el commit más grande | Señales de git | Sí |
| 1.2 | Evidencia de colaboración del equipo | Autores distintos y commits por autor, comparados con `integrantes`; un solo autor es alerta (las reglas exigen 2 o más integrantes) | Señales de git | Sí |
| 1.3 | El historial coincide con el proyecto final | Evolución del historial frente al código final | Claude Code | Parcial |
| 2.1 | Código estructurado y relevante, con poco código sin usar, duplicado o de relleno | Duplicados, código muerto, TODO, *lorem ipsum*, *placeholders* | MegaLinter (incluye jscpd) + Claude Code | Parcial |
| 2.2 | Decisiones técnicas intencionales para el proyecto | Arquitectura y elecciones de librerías | Claude Code | Parcial |
| 2.3 | Los integrantes pueden explicar su código y decisiones | — | — | No: revisión manual |
| 2.4 | Sin reutilización sustancial sin explicar de proyectos previos, hackathons o repositorios públicos | Coincidencias externas, similitud entre equipos, ideas en la web, commits previos al sprint | SCANOSS, Dolos, Claude Code + búsqueda web, señales de git | Parcial |
| 3.1 | La documentación explica el proyecto, su funcionalidad y uso | README (obligatorio en la entrega) y comentarios del código claros (criterio "Documentación" del manual) | Claude Code + markdownlint (MegaLinter) | Parcial |
| 3.2 | Instalación, dependencias y requisitos documentados | README y archivos de dependencias | Claude Code | Parcial |
| 3.3 | Lenguaje claro, conciso y específico del proyecto | README | Claude Code | Parcial |
| 3.4 | Código externo o generado y asistencia de IA declarados, integrados y entendidos | Declaración en README y PDF de prompts, frente a lo encontrado por SCANOSS y la búsqueda web; clasificación de código externo (ver [Plantillas, generadores y librerías](#plantillas-generadores-y-librerías)) | Claude Code | Parcial |
| 4.1 | El código coincide con la funcionalidad de la demo | Funciones descritas en la presentación (PDF exportado o `presentacion.md` extraído del deck) frente al código | Claude Code (con `presentacion`) o revisión manual | Parcial |
| 4.2 | Diseño y UX consideran al usuario | — | — | No: revisión manual |
| 4.3 | Sin API keys, secretos, credenciales ni datos privados en el repositorio | Hallazgos con historial de git, sin mostrar el valor | Betterleaks (MegaLinter) | Sí |

Toda marca automática es un borrador: la valida el revisor.

Los criterios del manual del concurso (*2026 NA AI Challenge Handbook*) para la revisión de código coinciden con este checklist: **autenticidad y limpieza** (estructura, legibilidad, esfuerzo original), **documentación** (comentarios del código claros) y **atribución** (cita explícita de librerías externas, fragmentos de otras fuentes y código generado con IA).

## Calendario del evento

Fuente: manual del concurso y respuestas de los organizadores. Hora de México (UTC−6, sin horario de verano).

| Fecha | Hito | Uso en el framework |
| --- | --- | --- |
| 24 oct, 16:30 | Inicio del sprint (fin del *kickoff*, 15:30 hora de Arizona) | `evento.inicio: 2026-10-24T16:30:00-06:00` |
| 5 nov, 23:59 | Cierre de la entrega final | `evento.fin: 2026-11-05T23:59:00-06:00` |
| 6–7 nov | Launchpads (pitch y demo en vivo) | Se recibe todo después del 7 nov a las 12:40 |
| 7–9 nov | Revisión de código | 4 equipos |
| 9 nov (noche) o 10 nov (mañana) | Entrega de las revisiones a los organizadores | Reportes en inglés (PDF + ZIP) |
| 10 nov | Aviso a finalistas | Los organizadores pueden ajustar el ranking con la revisión |

Si los organizadores toman el cierre en hora de Arizona (UTC−7), sería el 6 nov a las 00:59 de México: los commits en esa hora se reportan con su hora exacta para que el revisor decida.

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
| Contenedor de un solo uso | MegaLinter, Dolos |
| CLI directo | scc, SCANOSS, md-to-pdf |
| Servicio local (`localhost`) | Grafana (con plugin Infinity), Loki, Prometheus, servidor de archivos de `reports/`, orquestador (API + página Configuración) |
| Asistente | Claude Code |

Todo corre en el equipo del revisor; nada se publica en la web. La única variante es la [GitHub Action](#github-action-para-otros-repositorios): el dueño de un repositorio la agrega a su propio pipeline, sobre su propio código, que ya está en GitHub.

## Entrada

La entrada del sistema es un archivo `equipos.yml` con los datos del evento y la lista de equipos. Se edita a mano o desde la página **Configuración** del dashboard; ambos modifican el mismo archivo.

```yaml
evento:
  nombre: "Hackathon 2026"           # aparece en el dashboard y los reportes
  inicio: 2026-11-01T09:00:00-06:00  # detecta commits previos al evento (plagio)
  fin:    2026-11-03T18:00:00-06:00  # corte de la versión revisada y commits posteriores

equipos:
  - id: equipo-01                    # carpeta en repos/ y reports/, y filtro del dashboard
    nombre: "Equipo Alfa"
    proyecto: "Título del proyecto"
    objetivo: "Problema que resuelve y para quién"   # alimenta la búsqueda de plagio web
    repo: https://github.com/org/proyecto-alfa       # URL git
    rama: main                       # opcional, por defecto la rama principal
    commit: a1b2c3d                  # opcional: congela la versión entregada
    integrantes:                     # opcional: evidencia de colaboración (punto 1.2)
      - nombre: "Ana López"
        github: analopez
    presentacion: https://docs.google.com/presentation/d/…   # opcional: punto 4.1
    prompts_ia: C:/entregas/alfa-prompts.pdf                 # opcional: punto 3.4

  - id: equipo-02
    nombre: "Equipo Beta"
    proyecto: "…"
    objetivo: "…"
    repo: C:/entregas/beta.zip       # también carpeta local o ZIP
```

| Campo | Obligatorio | Uso |
| --- | --- | --- |
| `evento.nombre` | Sí | Título en dashboard y reportes |
| `evento.inicio`, `evento.fin` | Sí | Inicio y cierre del sprint de desarrollo. Señales de git: commits antes del inicio o después del cierre; `fin` también fija la versión revisada |
| `equipos[].id` | Sí | Identificador único; solo minúsculas, números y guiones |
| `equipos[].nombre`, `proyecto` | Sí | Etiquetas en dashboard y reportes |
| `equipos[].objetivo` | Sí | Palabras clave para la búsqueda de plagio en la web |
| `equipos[].repo` | Sí | URL git, carpeta local o archivo ZIP |
| `equipos[].rama`, `commit` | No | Fijan la versión revisada; sin `commit` se toma el último commit de la rama anterior a `evento.fin` |
| `equipos[].integrantes` | No | Nombre y, si se conoce, usuario de GitHub de cada integrante; se comparan con los autores de los commits por usuario o por nombre completo (punto 1.2). Un integrante sin usuario se lista igual y, si ningún commit coincide, cuenta como integrante sin commits |
| `equipos[].presentacion` | No | URL de la presentación entregada, ruta local a su PDF exportado, o ruta a `presentacion.md`: texto, notas del orador y descripción de las imágenes extraídos del deck por el revisor; se compara con el código (punto 4.1). Con solo la URL, 4.1 queda en revisión manual |
| `equipos[].prompts_ia` | No | Ruta local al PDF de prompts de IA entregado; se compara con lo declarado (punto 3.4) |

**Reglas:**

- `equipos.yml` contiene datos de los equipos: va en `.gitignore`. El repositorio solo incluye `equipos.example.yml`.
- Los commits posteriores a `evento.fin` no se revisan, pero se reportan en las señales de git.
- Las entregas del evento son repositorios públicos de GitHub (sin ZIP); carpeta local y ZIP quedan como respaldo.
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
| 7 | Plagio entre equipos | Dolos | 1 contenedor | Una vez, todos los repositorios juntos | Plagio | Grafana (Infinity) | Gratis |
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
| Seguridad del código | Semgrep | DevSkim |
| Secretos | Betterleaks | Gitleaks (deprecado en v9.6), TruffleHog, Secretlint, Kingfisher |
| Dependencias | Trivy | OSV-Scanner, Grype |
| Salen a internet con datos del repositorio | — | lychee (enlaces), Dustilock (registros de paquetes) |

**Requisitos de configuración (`.mega-linter.yml`):**

- Versión 10 o superior, para los dashboards de Grafana con "Why this rating?".
- Se monta una copia desechable del repositorio en la carpeta temporal del sistema (MegaLinter v10 escribe archivos temporales en el espacio de trabajo); `repos/<id>` nunca se monta ni se modifica y la copia se borra al terminar. Reportes escritos fuera de la copia.
- Imagen `ghcr.io/oxsecurity/megalinter:v10` (Docker Hub solo publica hasta v9).
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
| `reports/<equipo>/git-signals.json` | Script de git | Primer y último commit, número de commits, tamaño de cada commit y % del código en el más grande, autores y commits por autor, commits antes de `evento.inicio` o después de `evento.fin`, si es fork |

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

## Interfaz Next.js con JSON (Should)

Prioridad **Should** (ver [MOSCOW.md](MOSCOW.md)): una interfaz propia en Next.js que lee solo archivos JSON/CSV y no necesita Grafana, Prometheus ni Loki. Grafana local sigue siendo la interfaz **Must** del primer evento; esta la reemplaza cuando esté completa y permite publicar en Vercel más adelante.

**Por qué:** ninguna herramienta convierte dashboards de Grafana a Next.js (Perses migra con pérdidas y sus paquetes npm están poco documentados), y la mitad de las gráficas ya viene de archivos. Si todo sale como JSON, la interfaz es una sola app.

| Aspecto | Decisión |
| --- | --- |
| Framework | Next.js (App Router) + React + TypeScript |
| Gráficas | Librería de gráficas de React (por ejemplo, Recharts); mismas 6 páginas, mismas gráficas y mismo navbar que en Grafana |
| Datos de MegaLinter | Su reporte JSON local (`reports/<equipo>/megalinter.json`) en lugar de Prometheus/Loki |
| Resto de datos | Los mismos archivos de [Formato de resultados](#formato-de-resultados) |
| Configuración | Página propia en Next.js que usa la API del orquestador |
| Exportación | Mismos botones; descargan de `reports/` |
| Ejecución | `localhost`, sin cuentas |

**Camino a Vercel (Could, fuera del primer evento):**

- El análisis sigue local: Vercel no ejecuta contenedores ni análisis largos (límite por función de 300 s por defecto).
- Solo se suben los JSON de resultados a almacenamiento privado (Vercel Blob privado), nunca el código de los equipos.
- Publicar en la web exige login y revisar privacidad: reabre las decisiones de [Cómo se comparte](#cómo-se-comparte).
- Las subidas de ZIP de más de 4.5 MB irían directo a Blob, no a través de una función.

## Detección de plagio

El plagio se revisa en tres niveles y alimenta el punto 2.4 del checklist. Una coincidencia indica similitud, no copia confirmada: el revisor valida la evidencia y la decisión de descalificar es de los organizadores.

**Reglas del evento (confirmadas por los organizadores):**

- El trabajo debe ser original y hecho durante el sprint de dos semanas: **no se permite código previo**.
- **Se permite usar IA y recursos externos**, pero el código no puede ser íntegramente generado por IA: una persona del equipo lo revisa, lo valida y puede explicarlo. El uso de IA y de código externo se declara (punto 3.4).
- Una **semejanza sustancial** con un proyecto de otro hackatón o con un repositorio público **descalifica** al equipo.
- El reporte presenta evidencia (enlaces, porcentajes, historial de commits), no un veredicto. Ante similitudes marcadas, el revisor entrega esa evidencia a los organizadores, que deciden si hay sanción (protocolo acordado).
- Cada equipo tiene 2 o más integrantes: un repositorio con un solo autor es una alerta del punto 1.2.

### Plantillas, generadores y librerías

Respuesta de Arizona State University. Las marcas son orientativas: se conversan con los organizadores, que pueden subir o bajar una alerta.

| Origen del código | Tratamiento | Marca en el reporte |
| --- | --- | --- |
| Plantillas oficiales del concurso | Permitidas | Sin alerta; se identifica como plantilla oficial |
| Generadores de proyecto (`create-next-app`, Vite, etc.) | Generan dudas | Alerta a revisar: qué parte del repo es del generador y cuánto código propio hay encima |
| Librerías y fragmentos de terceros | Permitidos si se dan crédito y se citan | Sin cita (README o comentario): posible plagio, va al punto 2.4 con su evidencia |
| Código generado con IA | Permitido si se declara (PDF de prompts, README) | Sin declaración: se marca en el punto 3.4 |

Las dependencias instaladas por gestor de paquetes (`node_modules/`, *lockfiles*) no cuentan como código del equipo; las librerías copiadas dentro del repositorio sí se revisan.

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

**Señales en el historial de git:** commits anteriores al inicio del sprint, un solo commit o pocos commits con casi todo el código, commits posteriores al cierre, o un repositorio que es fork de otro.

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

**Puntos del checklist oficial:** propone la marca (cumple / no cumple / revisión manual) y la evidencia de los puntos 1.3, 2.1, 2.2, 2.4, 3.1–3.4 y 4.1. Lee el PDF de prompts (`prompts_ia`) y la presentación (`presentacion`) cuando existen, y clasifica el código externo según [Plantillas, generadores y librerías](#plantillas-generadores-y-librerías).

**Preguntas para el jurado:** 3 a 6 preguntas por equipo, concretas y con `archivo:línea`, para los puntos que el repositorio no resuelve (2.3, 4.1, 4.2) y para la *Lista de verificación de IA responsable* del manual, que el equipo debe poder explicar: propósito, autonomía, privacidad, sesgos y límites, accesibilidad y apoyo humano. Los organizadores las comparten con el jurado.

**Salida:** `review.json` en inglés (los textos que llegan al reporte), con referencias `archivo:línea` para cada hallazgo.

## Entregables

Jueces y organizadores reciben un reporte por equipo **en inglés** (idioma oficial del concurso), en PDF y Markdown, y un ZIP con todos; el dashboard de Grafana queda como herramienta local del revisor. Si los organizadores piden capturar el checklist en Airtable, el reporte es la fuente de la captura.

**Plantilla del reporte por equipo** (sigue el checklist oficial; los hallazgos de seguridad que el checklist no pide van como recomendaciones formativas que no puntúan):

```markdown
# Code Review Report – <Team> – <Project>

## Summary
- Languages and size (scc):
- Checklist: <n> pass · <n> fail · <n> manual review
- Key findings:

## Official checklist
| # | Item | Result | Evidence |
(1.1 to 4.3: Pass / Fail / Manual review, with links and file:line)

## Plagiarism and originality evidence
- External code (SCANOSS): files, source project, license, link, cited or not
- Between teams (Dolos): pairs and similarity %
- Similar projects on the web: link, date, level
- Git history: commits before/after the sprint, single commit, authors, fork
- Templates, generators and third-party code: classification

## Questions for the judges

## Reviewer feedback

## Technical annex (formative, not scored)
- Dependencies with known vulnerabilities (CVE, fixed version)
- Insecure code patterns (CWE / OWASP Top 10)
- Code quality (MegaLinter grade A–E)
- Responsible AI integration and OWASP LLM
```

Además del reporte por equipo, el ZIP incluye `judge-questions.md` con las preguntas de todos los equipos.

## GitHub Action para otros repositorios

Cualquier persona puede validar **su propio repositorio** con el mismo análisis estático, agregando la acción a un workflow de su repositorio. La acción corre en el runner de ese repositorio y deja el reporte ahí; este proyecto no recibe código ni reportes.

**Dos modos separados:**

| | Local (evento) | GitHub Action |
| --- | --- | --- |
| Entrada | `equipos.yml`, como hasta ahora | Entradas de la acción; **nunca** `equipos.yml` |
| Repositorios | Los de todos los equipos | Solo el del workspace, el del dueño del pipeline |
| Comandos | `acr validate`, `fetch`, `analyze`, etc. | Solo `acr ci` |

El flujo local no cambia: `equipos.yml`, `acr analyze`, Dolos, la revisión con Claude Code y el reporte del evento siguen igual, y ningún paso pasa a depender de GitHub. La acción reutiliza los mismos pasos con un solo equipo armado en memoria. `acr ci` no tiene opción `--config`: si el workspace trae un `equipos.yml`, lo ignora (no lo lee ni clona lo que liste) y lo avisa en el resumen. Así una acción integrada en otro repositorio nunca obtiene ni analiza código de terceros.

```yaml
# .github/workflows/code-review.yml en el repositorio que se quiere validar
name: Code review
on: [pull_request, workflow_dispatch]
permissions:
  contents: read
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0          # historial completo: lo necesitan las señales de git
      - uses: AdanRuiz/advanced-code-review@v1
        with:
          event-start: 2026-10-24T16:30:00-06:00   # opcional
          event-end: 2026-11-05T23:59:00-06:00     # opcional
          members: |                              # opcional: punto 1.2
            analopez: Ana López
          ai-review: true                                        # opcional: apagada por defecto
          openrouter-api-key: ${{ secrets.OPENROUTER_API_KEY }}  # requerida si ai-review es true
          ai-model: deepseek/deepseek-v4.1-flash                   # opcional
```

### Qué corre

| Paso | En la acción | Motivo |
| --- | --- | --- |
| Obtener | Copia del workspace a una carpeta temporal en solo lectura (como una carpeta local en [Entrada](#entrada)) | Mismo invariante de solo lectura |
| Señales de git | Sí | Puntos 1.1 y 1.2; `fork` se lee del evento de GitHub (`GITHUB_EVENT_PATH`), sin llamar a la API |
| scc | Sí | Contexto del reporte |
| MegaLinter | Sí, con el mismo `.mega-linter.yml` de este proyecto y el mismo endurecimiento (PB-40) | Puntos 2.1 y 4.3; anexo técnico |
| SCANOSS | Sí por defecto (`scanoss: false` lo apaga) | Punto 2.4; solo envía huellas |
| Dolos | No | Compara equipos entre sí; con un solo repositorio no aplica |
| Revisión con IA (OpenRouter) | Solo con `ai-review: true` y `openrouter-api-key` (ver [Revisión con IA vía OpenRouter](#revisión-con-ia-vía-openrouter-opcional)) | Puntos 1.3, 2.1, 2.2, 2.4, 3.1–3.4 y 4.1 como borrador; apagada quedan como **Manual review** y el costo sigue en cero |
| Búsqueda web de ideas parecidas | No | El modelo no navega; el punto 2.4 solo usa SCANOSS y git |
| Reporte | `reporte.md` en inglés con la plantilla de [Entregables](#entregables) | Mismo formato que el evento |

### Entradas y salidas

| Entrada | Por defecto | Uso |
| --- | --- | --- |
| `event-name` | Nombre del repositorio | Título del reporte |
| `project`, `objective` | Vacíos | Etiquetas del reporte |
| `event-start`, `event-end` | Sin ventana | Commits fuera del sprint; sin ventana, esa señal queda "sin dato" |
| `members` | Vacío | Una línea `usuario-github: Nombre` por integrante; sin integrantes, 1.2 compara solo número de autores |
| `scanoss` | `true` | Activa SCANOSS |
| `fail-on` | `none` | `none`: solo informa · `secrets`: falla si el punto 4.3 no cumple · `fail`: falla si algún punto no cumple |
| `retention-days` | `7` | Días que GitHub guarda el artifact |
| `artifact-name` | `acr-report` | Nombre del artifact (útil con una matriz de jobs) |
| `path` | El workspace | Carpeta del repositorio a validar, con su `.git` |
| `ai-review` | `false` | Activa la revisión con IA; el workflow tiene que pedirla de forma explícita |
| `openrouter-api-key` | Vacío | Key de OpenRouter, siempre desde un secreto del repositorio (`${{ secrets.OPENROUTER_API_KEY }}`); por sí sola no activa nada |
| `ai-model` | `deepseek/deepseek-v4.1-flash` | Modelo de OpenRouter para la revisión con IA |
| `ai-max-input-chars` | `200000` | Tope de texto del repositorio que se envía al modelo; limita el costo |

| Salida | Contenido |
| --- | --- |
| Resumen de la ejecución (`GITHUB_STEP_SUMMARY`) | Tabla del checklist (*Pass / Fail / Manual review*) y hallazgos clave |
| Artifact `acr-report` | `reporte.md`, JSON de resultados y SARIF de MegaLinter, Semgrep y Trivy sin fragmentos de código (`snippet`) y con lo que parezca credencial ocultado; sin las bitácoras de MegaLinter |
| Outputs del paso | `report-path`, `pass`, `fail`, `manual` y `errors` (pasos que fallaron) |

### Revisión con IA vía OpenRouter (opcional)

Sustituye a Claude Code cuando no hay una persona revisando: una sola llamada al modelo, sin herramientas ni navegación, que produce el mismo `review.json` que la [Revisión asistida](#revisión-asistida-con-claude-code).

- **Apagada por defecto:** solo corre si `ACR_AI_REVIEW` vale `true`. La acción la llena desde la entrada `ai-review` (por defecto `false`), así que el workflow de cada repositorio decide de forma explícita. Tener la key no basta para activarla.
- **Variables:** `ACR_AI_REVIEW` (activa), `OPENROUTER_API_KEY` (key) y `ACR_AI_MODEL` (modelo, por defecto `deepseek/deepseek-v4.1-flash`). La acción las llena desde `ai-review`, `openrouter-api-key` y `ai-model`.
- **Activada sin key:** el paso se omite con un aviso en el resumen y no hace fallar el pipeline. Pasa, por ejemplo, en *pull requests* desde forks, donde GitHub no entrega secretos.
- **También en local:** `acr ai-review [ID]` lee `equipos.yml` como los demás comandos y escribe en `reports/<id>/`, con la misma variable `ACR_AI_REVIEW`. No forma parte de `acr analyze`. En el evento envía código de los equipos a un tercero, así que solo se activa si los organizadores lo permiten; si no, se sigue con Claude Code.
- **Qué se envía:** el prompt de `acr/prompts/review.md` adaptado a una sola llamada, el árbol de archivos, el README, los archivos de dependencias y los archivos de código más relevantes hasta `ai-max-input-chars`, más un resumen de `scc.json`, `git-signals.json` y `scanoss.json`. **Nunca** se envían los archivos donde Betterleaks encontró un secreto ni archivos tipo `.env`, llaves o certificados. El contenido del repositorio va delimitado y marcado como datos, no instrucciones.
- **Privacidad del proveedor:** la petición pide a OpenRouter solo proveedores que no guardan ni entrenan con los datos (`provider.data_collection: "deny"`).
- **Salida:** la respuesta se pide en JSON y pasa por el mismo validador de `acr review-validate`. Si no es válida, se reintenta una vez con los errores; si sigue sin serlo, se descarta y los puntos quedan en *Manual review*. `review.json` registra el modelo usado (`generated_by`), con `status: "draft"`.
- **Costo:** el resumen de la ejecución muestra los tokens usados y el costo que devuelve OpenRouter.
- **La key nunca** aparece en el log, el resumen, el artifact ni los mensajes de error.
- **El PDF de prompts y la presentación** no se envían (no hay librería para leer PDF): 3.4 y 4.1 usan solo el README y el código.

### Reglas

- **Acción compuesta** (`action.yml` en la raíz): instala Python 3.12, `acr`, scc y scanoss-py con versiones fijas y corre `acr ci`. Solo runners Linux (MegaLinter necesita Docker); en otro sistema falla con un mensaje claro.
- **`acr ci`:** comando nuevo y el único que invoca la acción. Arma la configuración en memoria con un solo equipo, sin opción `--config` y sin leer `equipos.yml` aunque exista; solo copia el workspace (no clona ni descarga otros repositorios); usa como `id` el nombre del repositorio saneado (minúsculas, números y guiones) y escribe todo en `RUNNER_TEMP`, nunca en el workspace, para que el reporte no termine en un commit.
- **Clon superficial:** si el checkout no trae historial completo, la acción avisa y las señales de git quedan "sin dato" en lugar de dar un resultado engañoso.
- **No ejecuta** el código del repositorio: no instala dependencias ni compila.
- **Secretos:** el log, el resumen y el artifact siguen la misma regla que el reporte (Betterleaks con `--redact`, nunca el valor). En repositorios públicos el log y los artifacts son visibles para otros usuarios de GitHub.
- **Permisos:** solo `contents: read`; la acción no usa el token de GitHub. El único secreto es la key opcional de OpenRouter; sin ella todo lo demás funciona, también en *pull requests* desde forks.
- **Versiones:** las acciones de terceros que usa (`setup-python`, `upload-artifact`) se fijan por SHA; la imagen de MegaLinter, por versión. Se publica con tags semánticos (`v1.0.0`) y un tag móvil `v1`.
- **Formativo:** igual que en el evento, cada marca automática es un borrador; el resumen lo dice.
- **`fail-on` solo usa marcas deterministas** (1.1, 1.2 y 4.3): una marca de la IA nunca hace fallar el pipeline, porque el código revisado podría manipularla.

## Entorno, seguridad y privacidad

El código es de estudiantes y de terceros: se analiza aislado, sin ejecutarlo y sin publicarlo.

- **Contenedores:** Podman (gratis sin restricciones) o Docker Desktop si el uso es personal o educativo.
- **CLI:** scc, SCANOSS (Python).
- **Dolos en contenedor:** imagen oficial `ghcr.io/dodona-edu/dolos-cli` con versión fija. En Windows, instalar Dolos con npm exige compilar `@dodona/dolos-parsers` con Visual Studio Build Tools (3–5 GB); la imagen pesa ~400 MB. Se monta `repos/` en solo lectura, sin red (`--network=none`) y con el mismo endurecimiento que MegaLinter; solo lee archivos y escribe CSV.
- **Solo lectura:** `repos/<id>` queda sin permiso de escritura y ninguna herramienta lo modifica; MegaLinter trabaja sobre una copia desechable.
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
| GitHub Action: muchos repositorios agotan el límite gratuito de SCANOSS desde runners compartidos | `scanoss: false`; si SCANOSS falla, el reporte lo marca como resultado faltante y la acción no falla por eso |
| GitHub Action: un linter de MegaLinter carga archivos de configuración ejecutables del repositorio (por ejemplo `eslint.config.js`) | Revisar `.mega-linter.yml` antes de publicar la acción; el riesgo ya existe en local |
| GitHub Action: un cambio en este repositorio rompe el pipeline de otros | Tags semánticos; `v1` solo avanza con cambios compatibles |
| Revisión con IA: el código revisado incluye instrucciones para el modelo (*prompt injection*) | Contenido delimitado como datos, salida validada contra el esquema, marcas solo como borrador y fuera de `fail-on` |
| Revisión con IA: el modelo por defecto cambia de nombre, de precio o deja de existir en OpenRouter | `ai-model` lo cambia sin tocar código; si el modelo no existe, el paso se omite con un mensaje claro |
| Revisión con IA: un repositorio grande dispara el costo | Tope `ai-max-input-chars` y costo visible en el resumen |

**Resueltos con los organizadores:**

| Pregunta | Respuesta |
| --- | --- |
| ¿Cómo se entregan los repositorios? | Por Airtable, una entrega por equipo: URL de GitHub público, URL de la presentación (Google Slides), PDF del Business Model Canvas y PDF de prompts de IA. Sin ZIP. El repo debe tener README y no incluir información privada ni API keys. Cierre: 5 de noviembre, 11:59 p.m. |
| ¿Las reglas permiten plantillas, código previo o código generado con IA? | IA y recursos externos sí, con revisión humana y declarados; código previo no. Semejanza sustancial con otro proyecto descalifica (ver [Detección de plagio](#detección-de-plagio)) |
| ¿La detección de plagio es parte del rol de Code Reviewer? | Sí: el code review verifica originalidad, uso de IA y autenticidad |
| ¿Hay rúbrica oficial? | Para la revisión de código, el *Technical Code Review Checklist* (ver [Checklist oficial](#checklist-oficial-de-code-review)). La rúbrica de 10 criterios y 50 puntos es para el pitch y la usan los jueces |
| ¿Por qué canal se entregan los reportes? | Las calificaciones se capturan en Airtable; los jueces reciben por correo el enlace a sus hojas unas 48 horas antes del evento |
| ¿La seguridad es un criterio aparte? | No. Solo se exige que el repo no tenga información privada ni API keys (punto 4.3) |
| ¿Se permite la IA si se declara? ¿El revisor ve el PDF de prompts? | Sí, y el revisor recibe los PDF y la documentación de cada equipo |
| ¿Plantillas, generadores y librerías cuentan como código previo? | Ver [Plantillas, generadores y librerías](#plantillas-generadores-y-librerías) (respuesta de ASU) |
| Fechas del sprint | Ver [Calendario del evento](#calendario-del-evento) |
| ¿Sesión de preguntas o acceso a la demo? | Se comparten las presentaciones de los pitches; el revisor prepara preguntas para el jurado |
| ¿Formato e idioma del reporte? | En inglés |
| ¿Hallazgos de seguridad adicionales (CVE, CWE)? | Sí, como recomendaciones técnicas formativas que no puntúan |
| ¿Protocolo ante plagio? | El revisor entrega la evidencia (enlaces, porcentajes, historial de commits); los organizadores deciden |
| ¿Cuántos equipos y para cuándo? | 4 equipos; revisiones listas el 9 nov en la noche o el 10 nov en la mañana |

**Pendientes con los organizadores:**

- [ ] Acceso a Airtable o export con equipo, proyecto, repo, presentación e integrantes con usuario de GitHub.
- [ ] ¿El checklist se captura también en Airtable o basta el reporte PDF?
- [ ] ¿Habrá grabaciones de los pitches (puntos 2.3 y 4.1)?
- [ ] Confirmar el cierre: el manual dice 11:59 p.m. del 5 nov y la respuesta dice 11:59 a.m.; se asume p.m. hora de México.

## Fuentes

- [MegaLinter – linters soportados](https://megalinter.io/latest/supported-linters)
- [MegaLinter – dashboards de Grafana](https://megalinter.io/10.0.0/observability/grafana/)
- [MegaLinter – API Reporter](https://megalinter.io/10.0.0/reporters/ApiReporter/)
- [osskb.org (SCANOSS)](https://osskb.org/)
- [SCANOSS en OpenChain](https://openchain-project.github.io/OpenChain-KWG/en/guide/tools/9-scanoss/)
