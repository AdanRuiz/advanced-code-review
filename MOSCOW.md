# Priorización MoSCoW y fases

Prioridad de entrega de [SPEC.md](SPEC.md), agrupada en fases. Cada ítem es un issue de GitHub con la etiqueta `backlog`; el agente de [scripts/agent](scripts/agent/) los toma en orden.

**Fase actual:** 1
**Adelanto permitido:** Fase 2

- **Fase actual:** el agente toma primero los ítems de esta fase.
- **Adelanto permitido:** cuando no queda nada que pueda hacer en la fase actual, sigue con esta.
- Cambiar de fase es una decisión humana: se edita este archivo (el agente no puede modificarlo).
- Dentro de una fase el orden es Must → Should → Could y, a igual prioridad, número de issue.

## Resumen

Las columnas Must, Should y Could cuentan ítems del agente; Humano, los que hace una persona.

| Fase | Objetivo | Must | Should | Could | Humano |
| --- | --- | --- | --- | --- | --- |
| 0 | Preparación del entorno y del evento | — | — | — | 3 |
| 1 | Núcleo de análisis por línea de comandos | 11 | — | — | — |
| 2 | Dashboard Grafana local y exportación | 9 | — | — | — |
| 3 | Orquestador y página Configuración | — | 4 | — | — |
| 4 | Interfaz Next.js con JSON | — | 6 | 1 | 1 |
| 5 | Publicación (Vercel) | — | — | 3 | 1 |

Para el primer evento basta con las fases 0 a 2 (todo lo **Must**): análisis por línea de comandos, dashboard Grafana local y reportes exportados.

## Fase 0 — Preparación (humano)

| ID | Prioridad | Ítem | Aceptación |
| --- | --- | --- | --- |
| PB-1 | Must · humano | Instalar herramientas de análisis | `podman` (o Docker), `scc`, `scanoss-py`, Dolos y `md-to-pdf` responden a `--version` en la laptop |
| PB-2 | Must · humano | Confirmar pendientes con los organizadores | Respuestas a los pendientes de [SPEC.md](SPEC.md#riesgos-y-pendientes) registradas en el issue |
| PB-3 | Must · humano | Llenar `equipos.yml` del evento | Archivo con los equipos reales; `acr validate` sin errores |

## Fase 1 — Núcleo de análisis (Must)

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

## Fase 2 — Dashboard Grafana local y exportación (Must)

| ID | Prioridad | Ítem | Requisitos | Aceptación |
| --- | --- | --- | --- | --- |
| PB-15 | Must | Servicios locales | Dashboard local | `compose.yaml` con Grafana, Loki, Prometheus y servidor de archivos de `reports/`, solo en `localhost`, sin acceso anónimo |
| PB-16 | Must | Datos de MegaLinter hacia Grafana | MegaLinter | API Reporter apuntando a Loki/Prometheus locales; carga de los dashboards de MegaLinter |
| PB-17 | Must | Provisioning: Infinity, navbar y variables | Navegación | Datasource Infinity, *dashboard links* con la etiqueta `advanced-code-review` y variables `equipo` y `herramienta` |
| PB-18 | Must | Página Resumen | 1. Resumen | Paneles de la spec, incluido estado del análisis |
| PB-19 | Must | Página Equipo | 2. Equipo | Paneles de la spec |
| PB-20 | Must | Páginas Calidad y Calificación | 3. Calidad, 6. Calificación | Paneles de la spec y aviso de la fórmula |
| PB-21 | Must | Página Seguridad | 4. Seguridad | CVE por severidad desde SARIF de Trivy, CWE/OWASP desde Semgrep, indicador de CVE críticas/altas con corrección |
| PB-22 | Must | Página Plagio | 5. Plagio | Matriz de Dolos, SCANOSS, web y señales de git |
| PB-23 | Must | Exportación de reportes | Exportación de reportes | `reporte.pdf` por equipo con md-to-pdf, `todos.zip`, y botones del navbar que los descargan |

## Fase 3 — Orquestador y Configuración (Should)

| ID | Prioridad | Ítem | Requisitos | Aceptación |
| --- | --- | --- | --- | --- |
| PB-24 | Should | API de evento y equipos | API del orquestador | Endpoints de evento y CRUD de equipos sobre `equipos.yml`; solo `localhost`; tests |
| PB-25 | Should | Subir ZIP y validar origen | API del orquestador | `POST /api/equipos/{id}/zip` y `/validar`; mismas protecciones que PB-6 |
| PB-26 | Should | Lanzar análisis y consultar estado | API del orquestador | `POST /api/analisis[/{id}]` en segundo plano y `GET /api/analisis/estado`; un análisis a la vez por equipo |
| PB-27 | Should | Página Configuración y botón ⚙ | Página Configuración | Página servida por el orquestador con las 6 secciones de la spec; enlace desde el navbar de Grafana |

## Fase 4 — Interfaz Next.js con JSON (Should)

| ID | Prioridad | Ítem | Requisitos | Aceptación |
| --- | --- | --- | --- | --- |
| PB-28 | Should · humano | Aprobar dependencias de la app web | Interfaz Next.js | `web/package.json` y lockfile con Next.js, React, TypeScript y librería de gráficas, revisados por una persona |
| PB-29 | Should | Capa de datos JSON | Interfaz Next.js | Lectura y tipado de todos los archivos de `reports/`, incluido `megalinter.json`; tests |
| PB-30 | Should | Layout, navbar y selector de equipo | Navegación | Mismo navbar y botones de exportación que en Grafana |
| PB-31 | Should | Páginas Resumen y Equipo | 1. Resumen, 2. Equipo | Mismas gráficas que en Grafana |
| PB-32 | Should | Páginas Calidad y Calificación | 3. Calidad, 6. Calificación | Mismas gráficas que en Grafana |
| PB-33 | Should | Páginas Seguridad y Plagio | 4. Seguridad, 5. Plagio | Mismas gráficas que en Grafana |
| PB-34 | Should | Página Configuración en Next.js | Interfaz Next.js | Usa la API del orquestador (fase 3) |
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
| SonarQube | Límite de líneas en la nube y licencia de analizadores; MegaLinter cubre calidad |
| DefectDojo | Excesivo para pocos equipos; orientado a seguridad, no a calidad |
| Grafana Cloud | Límite de 3 usuarios en el plan gratuito |
| Plugin Business Forms | Figura como plugin de pago en el catálogo de Grafana |
| Publicar Grafana en la web | Decisión: el dashboard es local (ver [Cómo se comparte](SPEC.md#cómo-se-comparte)) |
| Conversión automática Grafana → Next.js (Perses) | Conversión con pérdidas y poca documentación |
