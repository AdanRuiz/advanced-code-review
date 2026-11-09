# Priorización MoSCoW y fases

Prioridad de entrega de [SPEC.md](SPEC.md), agrupada en fases. Cada ítem es un issue de GitHub con la etiqueta `backlog`; el agente de [scripts/agent](scripts/agent/) los toma en orden.

**Fase actual:** 2
**Adelanto permitido:** Fase 3

- **Fase actual:** el agente toma primero los ítems de esta fase.
- **Adelanto permitido:** cuando no queda nada que pueda hacer en la fase actual, sigue con esta.
- Cambiar de fase es una decisión humana: se edita este archivo (el agente no puede modificarlo).
- Dentro de una fase el orden es Must → Should → Could y, a igual prioridad, número de issue.

## Meta del primer evento

**Entregar el 9 de noviembre en la noche** (a más tardar el 10 en la mañana) los reportes en inglés de los 4 equipos, en PDF y en un ZIP, según el checklist oficial (ver [Calendario del evento](SPEC.md#calendario-del-evento)). Todo lo **Must** sirve a esa entrega. El dashboard de Grafana es una herramienta del revisor: ayuda, pero los organizadores reciben los reportes, así que pasa a **Should**.

## Resumen

Las columnas Must, Should y Could cuentan ítems del agente; Humano, los que hace una persona.

| Fase | Objetivo | Must | Should | Could | Humano |
| --- | --- | --- | --- | --- | --- |
| 0 | Preparación del entorno y del evento | — | — | — | 3 |
| 1 | Núcleo de análisis por línea de comandos ✅ | 12 | — | — | — |
| 2 | Reporte del evento según el checklist oficial | 6 | — | — | — |
| 3 | Dashboard Grafana local y orquestador | — | 8 | 4 | — |
| 4 | Interfaz Next.js con JSON | — | — | 7 | 1 |
| 5 | Publicación (Vercel) | — | — | 3 | 1 |

## Fase 0 — Preparación (humano)

| ID | Prioridad | Ítem | Aceptación |
| --- | --- | --- | --- |
| PB-1 | Must · humano | Instalar herramientas de análisis | `podman` (o Docker), `scc`, `scanoss-py`, Dolos y `md-to-pdf` responden a `--version` en la laptop |
| PB-2 | Must · humano | Confirmar pendientes con los organizadores | Respuestas registradas en el issue y en [SPEC.md](SPEC.md#riesgos-y-pendientes) |
| PB-3 | Must · humano | Llenar `equipos.yml` del evento | Los 4 equipos con fechas del [calendario](SPEC.md#calendario-del-evento), integrantes con usuario de GitHub, presentación exportada a PDF y PDF de prompts en `entregas/`; `acr validate` sin errores |

## Fase 1 — Núcleo de análisis (Must) ✅

Paquete Python `acr` con una CLI. Cada ítem trae tests que no requieren red ni las herramientas instaladas (se simulan los procesos externos).

| ID | Prioridad | Ítem | Requisitos | Aceptación |
| --- | --- | --- | --- | --- |
| PB-4 | Must | Esqueleto del paquete y CLI | Arquitectura y flujo | `python -m acr --help` lista los comandos; `ruff` y `pytest` en verde |
| PB-5 | Must | Cargar y validar `equipos.yml` | Entrada | `acr validate` reporta campos faltantes, `id` inválidos o repetidos y fechas mal formadas; tests por regla |
| PB-6 | Must | Obtener repositorios: URL git, carpeta y ZIP | Entrada, Entorno | `acr fetch` deja cada equipo en `repos/<id>`; respeta `rama` y `commit`; ZIP protegido contra *zip slip* y con límite de tamaño |
| PB-7 | Must | Señales de git | Detección de plagio | `reports/<id>/git-signals.json` con primer y último commit, número de commits y commits fuera de `evento.inicio`/`fin` |
| PB-8 | Must | Ejecutar scc | Stack, paso 1 | `reports/<id>/scc.json` con lenguajes, líneas y archivos |
| PB-9 | Must | Configuración y ejecución de MegaLinter | MegaLinter | `.mega-linter.yml` con los linters activos y desactivados de la spec; ejecución con repo en solo lectura; SARIF, JSON y Markdown en `reports/<id>/` |
| PB-10 | Must | Ejecutar SCANOSS | Detección de plagio | `reports/<id>/scanoss.json`; solo envía huellas; excluye dependencias |
| PB-11 | Must | Ejecutar Dolos sobre todos los equipos | Detección de plagio | `reports/dolos/pairs.csv` con similitud por par |
| PB-12 | Must | Comando `acr analyze` | Arquitectura y flujo | Corre los pasos en orden para uno o todos los equipos; `reports/status.json` con estado por equipo; un análisis a la vez por equipo |
| PB-13 | Must | Formato de la revisión asistida | Revisión asistida | Esquemas JSON y validador para `review.json` y `web-plagiarism.json`; prompt para Claude Code con la checklist de IA y OWASP LLM |
| PB-14 | Must | Reporte Markdown por equipo | Entregables | `reports/<id>/reporte.md` con la plantilla de la spec, a partir de todos los resultados disponibles |
| PB-40 | Must | Endurecer el contenedor de MegaLinter | Entorno | Contenedor sin capacidades extra, sin escalar privilegios y con límites de procesos y memoria |

## Fase 2 — Reporte del evento según el checklist oficial (Must)

Lo que falta para que el reporte responda al [checklist oficial](SPEC.md#checklist-oficial-de-code-review) con las reglas confirmadas por los organizadores. Orden pensado para la fecha de entrega.

| ID | Prioridad | Ítem | Requisitos | Aceptación |
| --- | --- | --- | --- | --- |
| PB-56 | Must | Campos del checklist en `equipos.yml` | Entrada | `acr validate` carga y valida `integrantes` (nombre y usuario de GitHub), `presentacion` (URL o PDF local) y `prompts_ia` (PDF local existente); avisa si faltan; tests por regla |
| PB-57 | Must | Señales de git para los puntos 1.1 y 1.2 | Checklist 1.1, 1.2; Formato de resultados (`git-signals.json`) | `git-signals.json` agrega líneas cambiadas por commit, % del código en el commit más grande, autores y commits por autor comparados con `integrantes`, alerta de un solo autor y si es fork; tests con historiales simulados |
| PB-58 | Must | Prompt de revisión según el checklist oficial | Revisión asistida; Plantillas, generadores y librerías | El prompt y `review.schema.json` piden en inglés la marca y evidencia de los puntos 1.3, 2.1, 2.2, 2.4, 3.1–3.4 y 4.1, la clasificación del código externo (plantilla oficial / generador / tercero citado o no / IA declarada o no), la lectura del PDF de prompts y de la presentación, y 3–6 preguntas para el jurado; tests del validador |
| PB-59 | Must | Reporte en inglés según el checklist oficial | Entregables | `reporte.md` con la plantilla en inglés de la spec: tabla del checklist (Pass / Fail / Manual review con evidencia), evidencia de plagio, preguntas para el jurado, *Reviewer feedback* y anexo técnico que no puntúa; `reports/judge-questions.md` con todos los equipos; tests |
| PB-23 | Must | Exportación de reportes | Exportación de reportes | `reporte.pdf` por equipo con md-to-pdf, `todos.zip` (reportes, `judge-questions.md` y CSV de Dolos) y botones del navbar que los descargan |
| PB-60 | Must | Dolos en contenedor | Entorno, seguridad y privacidad (Dolos en contenedor); Detección de plagio | `acr dolos` corre la imagen `ghcr.io/dodona-edu/dolos-cli` con versión fija (`--image` para cambiarla) con `podman` o `docker`, `repos/` montado en solo lectura, `--network=none` y el endurecimiento de PB-40; rutas del CSV de entrada traducidas al contenedor; mismas salidas en `reports/dolos/`; si no hay motor de contenedores, error claro; tests sin red ni contenedor |

## Fase 3 — Dashboard Grafana local (Should) y orquestador (Could)

Herramientas del revisor. El dashboard ayuda a revisar a los 4 equipos; el orquestador sobra con la CLI para un evento de este tamaño.

| ID | Prioridad | Ítem | Requisitos | Aceptación |
| --- | --- | --- | --- | --- |
| PB-16 | Should | Datos de MegaLinter hacia Grafana | MegaLinter | API Reporter apuntando a Loki/Prometheus locales; carga de los dashboards de MegaLinter |
| PB-41 | Should | Página Resumen: panel Estado del análisis | 1. Resumen | Tabla desde `reports/status.json` por Infinity, con color por estado |
| PB-42 | Should | Página Resumen: panel Alertas de plagio | 1. Resumen | Coincidencias por equipo en SCANOSS, Dolos y web |
| PB-46 | Should | Página Equipo: paneles de archivos | 2. Equipo | Paneles de `scc.json` y `review.json` por Infinity |
| PB-21 | Should | Página Seguridad | 4. Seguridad | CVE por severidad desde SARIF de Trivy, CWE/OWASP desde Semgrep, indicador de CVE críticas/altas con corrección |
| PB-43 | Should | Página Resumen: paneles de MegaLinter | 1. Resumen | Depende de PB-16 |
| PB-47 | Should | Página Equipo: paneles de MegaLinter | 2. Equipo | Depende de PB-16 |
| PB-20 | Should | Páginas Calidad y Calificación | 3. Calidad, 6. Calificación | Paneles de la spec y aviso de la fórmula; depende de PB-16 |
| PB-24 | Could | API de evento y equipos | API del orquestador | Endpoints de evento y CRUD de equipos sobre `equipos.yml`; solo `localhost`; tests |
| PB-25 | Could | Subir ZIP y validar origen | API del orquestador | `POST /api/equipos/{id}/zip` y `/validar`; mismas protecciones que PB-6 |
| PB-26 | Could | Lanzar análisis y consultar estado | API del orquestador | `POST /api/analisis[/{id}]` en segundo plano y `GET /api/analisis/estado`; un análisis a la vez por equipo |
| PB-27 | Could | Página Configuración y botón ⚙ | Página Configuración | Página servida por el orquestador con las 6 secciones de la spec; enlace desde el navbar de Grafana |

## Fase 4 — Interfaz Next.js con JSON (Could)

Después del primer evento.

| ID | Prioridad | Ítem | Requisitos | Aceptación |
| --- | --- | --- | --- | --- |
| PB-28 | Could · humano | Aprobar dependencias de la app web | Interfaz Next.js | `web/package.json` y lockfile con Next.js, React, TypeScript y librería de gráficas, revisados por una persona |
| PB-29 | Could | Capa de datos JSON | Interfaz Next.js | Lectura y tipado de todos los archivos de `reports/`, incluido `megalinter.json`; tests |
| PB-30 | Could | Layout, navbar y selector de equipo | Navegación | Mismo navbar y botones de exportación que en Grafana |
| PB-31 | Could | Páginas Resumen y Equipo | 1. Resumen, 2. Equipo | Mismas gráficas que en Grafana |
| PB-32 | Could | Páginas Calidad y Calificación | 3. Calidad, 6. Calificación | Mismas gráficas que en Grafana |
| PB-33 | Could | Páginas Seguridad y Plagio | 4. Seguridad, 5. Plagio | Mismas gráficas que en Grafana |
| PB-34 | Could | Página Configuración en Next.js | Interfaz Next.js | Usa la API del orquestador (fase 3) |
| PB-35 | Could | Modo claro/oscuro y vista imprimible | Exportación de reportes | Cada página se imprime limpia a PDF desde el navegador |

## Fase 5 — Publicación (Could)

| ID | Prioridad | Ítem | Requisitos | Aceptación |
| --- | --- | --- | --- | --- |
| PB-36 | Could · humano | Decidir publicación y revisar privacidad | Camino a Vercel | Decisión escrita: quién accede, qué datos suben y por cuánto tiempo |
| PB-37 | Could | Subida de resultados a almacenamiento privado | Camino a Vercel | Solo JSON de resultados, nunca código; script de subida con tests |
| PB-38 | Could | Login para la app publicada | Camino a Vercel | Sin acceso anónimo; cuentas de jueces y organizadores |
| PB-39 | Could | Despliegue en Vercel | Camino a Vercel | App publicada con datos de prueba, sin código de equipos |

## Won't (esta versión)

| Ítem | Motivo |
| --- | --- |
| Análisis dinámico (ejecutar la app, *prompt injection*, accesibilidad) | Va en un framework aparte |
| Determinar si el código fue generado con IA | No hay método confiable; se revisa que esté declarado (punto 3.4) |
| Captura automática en Airtable | Pendiente de acceso; si se pide, se captura a mano desde el reporte |
| SonarQube | Límite de líneas en la nube y licencia de analizadores; MegaLinter cubre calidad |
| DefectDojo | Excesivo para pocos equipos; orientado a seguridad, no a calidad |
| Grafana Cloud | Límite de 3 usuarios en el plan gratuito |
| Plugin Business Forms | Figura como plugin de pago en el catálogo de Grafana |
| Publicar Grafana en la web | Decisión: el dashboard es local (ver [Cómo se comparte](SPEC.md#cómo-se-comparte)) |
| Conversión automática Grafana → Next.js (Perses) | Conversión con pérdidas y poca documentación |
