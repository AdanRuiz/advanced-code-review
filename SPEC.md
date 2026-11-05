# Advanced Code Review – Especificación

Framework de revisión estática de repositorios para hackathons y concursos de programación.

Versión: 2026-11-04

## Propósito y alcance

El framework revisa de forma estática los repositorios de los equipos participantes con herramientas open source y sin costo adicional, sin importar el lenguaje en que estén programados.

**Criterios que cubre:**

| Criterio | Qué se evalúa |
| --- | --- |
| Factibilidad técnica | Lenguajes, tamaño, arquitectura, dependencias vulnerables o abandonadas |
| Calidad del código | Hallazgos de linters, patrones inseguros, mantenibilidad |
| Integración responsable de IA | Secretos expuestos, datos enviados al proveedor de IA, filtros, manejo de errores, supervisión humana |
| Plagio | Código copiado de proyectos públicos, similitud entre equipos, ideas o proyectos ya existentes en la web |

**Fuera de alcance:**

- Análisis dinámico (ejecutar la app, pruebas de *prompt injection*, accesibilidad de la interfaz): se cubre en un framework aparte.
- Evaluación del problema, impacto y pitch: la hacen los otros jueces.

## Arquitectura y flujo

Cada repositorio se clona una vez en solo lectura y cada herramienta lo lee una sola vez. Solo MegaLinter y el dashboard usan contenedores.

1. **Clonar** los repositorios en `repos/<equipo>` (solo lectura).
2. **Reconocer** con scc: lenguajes, líneas y archivos por repositorio.
3. **MegaLinter** (1 contenedor por repositorio): calidad, Semgrep, Betterleaks y Trivy en una sola pasada. Envía resultados a Grafana y guarda SARIF/Markdown en `reports/<equipo>/`.
4. **SCANOSS** por repositorio: coincidencias con código open source.
5. **Dolos** una sola vez sobre todos los repositorios: similitud entre equipos.
6. **Claude Code** lee `reports/<equipo>/` (sin volver a escanear), revisa el código y hace la búsqueda de plagio en la web.
7. **Exportar** los resultados de scc, SCANOSS, Dolos y Claude Code como JSON/CSV en `reports/` (ver [Formato de resultados](#formato-de-resultados)).
8. **Publicar**: dashboard en Grafana (Prometheus, Loki y archivos vía Infinity) y reporte Markdown por equipo como respaldo.

| Corre como | Componentes |
| --- | --- |
| Contenedor de un solo uso | MegaLinter |
| CLI directo | scc, SCANOSS, Dolos |
| Servicio permanente | Grafana (con plugin Infinity), Loki, Prometheus, servidor de archivos de `reports/` (solo red interna) |
| Asistente | Claude Code |

## Stack de componentes

| # | Paso | Herramienta | Cómo corre | Alcance | Criterio | Destino | Costo |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Reconocimiento de lenguaje y tamaño | [scc](https://github.com/boyter/scc) | CLI | Por repositorio | Factibilidad | Grafana (Infinity) | Gratis |
| 2 | Calidad de código | [MegaLinter](https://megalinter.io/latest/supported-linters) (linters del lenguaje detectado) | 1 contenedor | Por repositorio | Calidad | Grafana | Gratis |
| 3 | Seguridad del código | Semgrep (en MegaLinter) | Mismo contenedor | Por repositorio | Calidad | Grafana | Gratis |
| 4 | Secretos expuestos | Betterleaks (en MegaLinter) | Mismo contenedor | Por repositorio, con historial git | IA responsable | Grafana | Gratis |
| 5 | Dependencias vulnerables | Trivy (en MegaLinter) | Mismo contenedor | Por repositorio | Factibilidad | Grafana | Gratis |
| 6 | Plagio de código externo | [SCANOSS](https://osskb.org/) (osskb.org) | CLI | Por repositorio | Plagio | Grafana (Infinity) | Gratis, con límite de uso |
| 7 | Plagio entre equipos | Dolos | CLI | Una vez, todos los repositorios juntos | Plagio | Grafana (Infinity) | Gratis |
| 8 | Revisión asistida | Claude Code (`/code-review`, `/security-review`) | Lee reportes 1–7 | Por repositorio | Factibilidad, calidad, IA responsable | Grafana (Infinity) + reporte Markdown | Suscripción existente |
| 9 | Plagio de ideas en la web | Claude Code + búsqueda web | Búsqueda por título, objetivo y palabras clave | Por proyecto | Plagio | Grafana (Infinity) | Suscripción existente |
| — | Dashboard compartido | Grafana + Loki + Prometheus + [Infinity](https://grafana.com/docs/plugins/yesoreyeram-infinity-datasource/latest/) (Apache 2.0) | Servicio permanente | Todos los equipos | Presentación | Jueces y organizadores | Gratis |

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

## Dashboard compartido

Grafana reúne en 6 páginas, unidas por un navbar, todos los resultados del framework: calidad y seguridad (MegaLinter) y tamaño, plagio y revisión asistida (archivos JSON/CSV).

### Componentes

| Componente | Función |
| --- | --- |
| Grafana | Interfaz, dashboards y cuentas de usuario |
| Prometheus | Métricas numéricas de MegaLinter por repositorio y linter |
| Loki | Detalle de hallazgos de MegaLinter: corridas, linters, reglas y archivos |
| Plugin Infinity | Lee los JSON/CSV de scc, SCANOSS, Dolos y Claude Code por URL |
| Servidor de archivos | Sirve `reports/` en solo lectura a Grafana, solo dentro de la red interna |

### Navegación

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Advanced Code Review                                Equipo: [Todos ▾]    │
│ [Resumen] [Equipo] [Calidad] [Seguridad] [Plagio] [Calificación]         │
└──────────────────────────────────────────────────────────────────────────┘
```

- **Navbar:** *dashboard links* de Grafana; todas las páginas llevan la etiqueta `advanced-code-review` y se muestran como botones arriba.
- **Variable `equipo`:** selector Todos / uno / varios; se conserva al cambiar de página (*include current template variable values*).
- **Variable `herramienta`:** solo en Calidad y Seguridad, filtra por linter.
- **Clic en un equipo** en cualquier tabla abre la página Equipo filtrada.

Origen de datos: **ML** = MegaLinter (Prometheus/Loki, nativo) · **Archivo** = JSON/CSV vía Infinity.

### 1. Resumen: comparar a todos los equipos

| Sección | Gráfica | Qué muestra | Origen |
| --- | --- | --- | --- |
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
| Indicadores | 3 tarjetas rojo/verde | Hallazgos de Semgrep · secretos (Betterleaks) · dependencias vulnerables (Trivy) | ML |
| Por equipo | Barras agrupadas | Las 3 categorías por equipo | ML |
| Secretos expuestos | Tabla | Equipo, archivo y tipo de secreto, **sin mostrar el valor** | ML (Loki) |
| Patrones inseguros | Tabla | Reglas de Semgrep activadas, con archivo y línea | ML (Loki) |
| Dependencias | Tabla | Paquete, versión y vulnerabilidad (CVE) | ML (Loki) |

MegaLinter no envía la severidad como métrica; separar por severidad requiere leer el SARIF de Semgrep y Trivy como Archivo.

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
| `reports/dolos/pairs.csv` | Dolos | Similitud por par de equipos |
| `reports/<equipo>/review.json` | Claude Code | Hallazgos: criterio, gravedad, archivo, línea, descripción; checklist de IA |
| `reports/<equipo>/web-plagiarism.json` | Claude Code | Proyectos encontrados: enlace, fecha, nivel de parecido |
| `reports/<equipo>/git-signals.json` | Script de git | Fecha del primer commit, número de commits, si es fork |

Limitaciones de Infinity: no está pensado para volúmenes grandes de datos (suficiente para un hackathon) y lee por URL, por eso `reports/` se sirve con un servidor de archivos interno.

### Acceso

- Una cuenta de solo lectura (rol *Viewer*) para cada juez y organizador; el revisor como administrador.
- El servicio debe estar en un servidor o red accesible para ellos; en una laptop solo lo ve su dueño.
- Sin acceso anónimo ni enlaces públicos: el código es de estudiantes.

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

**Salida:** sección cualitativa del reporte por equipo, con referencias `archivo:línea` para cada hallazgo.

## Entregables

Se entregan dos cosas a jueces y organizadores: el dashboard de Grafana con los resultados de todos los pasos y, como respaldo, un reporte Markdown por equipo.

**Plantilla del reporte por equipo:**

```markdown
# Reporte de revisión – <Equipo> – <Proyecto>

## Resumen
- Lenguajes y tamaño (scc):
- Enlace al dashboard (Grafana):
- Hallazgos críticos:

## Factibilidad técnica
## Calidad del código
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
| Grafana no accesible para los jueces | Compartir los reportes SARIF/Markdown como respaldo |
| Falsos positivos de las herramientas o de la IA | Toda conclusión la valida el revisor |

**Pendientes con los organizadores:**

- [ ] ¿Cómo se entregan los repositorios (GitHub, invitación, zip)?
- [ ] ¿Las reglas permiten plantillas, código previo o código generado con IA?
- [ ] ¿La detección de plagio es parte del rol de Code Reviewer?
- [ ] ¿Hay rúbrica oficial del evento para alinear los reportes?
- [ ] ¿Cuántos jueces tendrán acceso al dashboard?

## Fuentes

- [MegaLinter – linters soportados](https://megalinter.io/latest/supported-linters)
- [MegaLinter – dashboards de Grafana](https://megalinter.io/10.0.0/observability/grafana/)
- [MegaLinter – API Reporter](https://megalinter.io/10.0.0/reporters/ApiReporter/)
- [osskb.org (SCANOSS)](https://osskb.org/)
- [SCANOSS en OpenChain](https://openchain-project.github.io/OpenChain-KWG/en/guide/tools/9-scanoss/)
