<p align="center">
  <img src="advanced-code-review.svg" alt="Advanced Code Review" width="280">
</p>

# Advanced Code Review

Framework open source para revisar de forma estática repositorios de hackathons, sin importar el lenguaje en que estén programados.

> **Estado:** especificación lista; implementación por fases en curso. Diseño en [SPEC.md](SPEC.md), prioridades y fases en [MOSCOW.md](MOSCOW.md). Un [agente de Claude Code](docs/agente.md) toma los issues del backlog y abre un PR por ítem.

## Qué evalúa

| Criterio | Qué se revisa |
| --- | --- |
| Factibilidad técnica | Lenguajes, tamaño, arquitectura y dependencias |
| Calidad del código | Hallazgos de linters, mantenibilidad |
| Seguridad | Código inseguro (CWE / OWASP Top 10), dependencias con CVE, secretos expuestos |
| Integración responsable de IA | Secretos expuestos, datos enviados al proveedor de IA, filtros y supervisión humana |
| Plagio | Código copiado de proyectos públicos, similitud entre equipos e ideas ya existentes en la web |

El análisis dinámico (ejecutar la app) queda fuera de este proyecto.

## Stack

Todo el stack es open source y gratuito.

| Herramienta | Función |
| --- | --- |
| [scc](https://github.com/boyter/scc) | Detecta lenguajes y tamaño de cada repositorio |
| [MegaLinter](https://megalinter.io/) | Calidad de código, Semgrep, Betterleaks (secretos) y Trivy (dependencias) en una sola pasada |
| [SCANOSS](https://osskb.org/) | Plagio de código open source conocido (envía solo huellas, no el código) |
| [Dolos](https://dolos.ugent.be/) | Similitud de código entre equipos |
| Grafana + Loki + Prometheus + Infinity | Dashboard local de 6 páginas con navbar: resumen, equipo, calidad, seguridad, plagio y calificación |
| [md-to-pdf](https://github.com/simonhaenisch/md-to-pdf) | Exporta el reporte de cada equipo a PDF; los botones del dashboard lo descargan |
| Claude Code | Revisión asistida y búsqueda de plagio de ideas en la web |

Claude Code requiere una suscripción propia.

## Flujo

1. Cargar los equipos en `equipos.yml` (ver [equipos.example.yml](equipos.example.yml)) o desde la página Configuración del dashboard, y obtener cada repositorio (URL git, carpeta o ZIP) una vez, en solo lectura.
2. Reconocer lenguaje y tamaño con scc.
3. Correr MegaLinter (un contenedor) y enviar los resultados a Grafana.
4. Correr SCANOSS por repositorio y Dolos sobre todos los repositorios juntos.
5. Hacer la revisión asistida con Claude Code a partir de los reportes generados.
6. Entregar el dashboard y un reporte Markdown por equipo.

## Uso

Requiere Python 3.12 y las dependencias de `requirements-dev.txt`.

```sh
python -m acr --help                 # lista los comandos
python -m acr validate               # valida equipos.yml (PB-5)
python -m acr fetch [ID]             # obtiene los repositorios en repos/<id> (PB-6)
python -m acr git-signals [ID]       # señales del historial de git (PB-7)
python -m acr scc [ID]               # lenguajes, líneas y archivos con scc (PB-8)
python -m acr megalinter [ID]        # calidad, Semgrep, Betterleaks y Trivy con MegaLinter (PB-9)
python -m acr scanoss [ID]           # código open source conocido con SCANOSS, solo huellas (PB-10)
python -m acr dolos                  # similitud de código entre todos los equipos con Dolos (PB-11)
python -m acr analyze [ID]           # corre el análisis completo (PB-12)
python -m acr --config otro.yml ...  # usa otro archivo de entrada
```

`acr validate` revisa `equipos.yml` y lista todos los errores a la vez (campos faltantes, `id` inválidos o repetidos, fechas mal formadas o `fin` anterior a `inicio`); sale con código 0 si el archivo es válido y 1 si no.

`acr fetch` deja cada equipo en `repos/<id>` según su `repo`:

- **URL git** (`https://`, `http://`, `ssh://`, `git://` o `usuario@host:ruta`): `git clone` con historial completo, en la rama `rama` si se indica y fijado en `commit` si se indica. Usa las credenciales de git del revisor; los enlaces simbólicos se obtienen como archivos normales. Otros esquemas (`file://`, `ext::`) se rechazan.
- **Carpeta local:** se copia sin seguir enlaces simbólicos.
- **ZIP** (`.zip`): se descomprime rechazando rutas peligrosas (*zip slip*) y enlaces simbólicos, con un límite de tamaño descomprimido (`--max-zip-mb`, 1024 MB por defecto) y de 100 000 entradas.

`rama` y `commit` solo aplican a URL git. Cada equipo se obtiene en una carpeta temporal y reemplaza a `repos/<id>` solo si todo salió bien; los archivos quedan en solo lectura. Si un equipo falla, se reporta y se sigue con los demás (código de salida 1). Las rutas relativas se toman desde el directorio actual. El código obtenido nunca se ejecuta.

`acr git-signals` lee el historial de `repos/<id>` (solo `git rev-parse` y `git log` sobre la versión obtenida, sin ejecutar el código) y escribe `reports/<id>/git-signals.json` con:

- `first_commit` y `last_commit` (hash, fecha de autor y fecha de commit), `commit_count` y `single_commit` (un solo commit con todo el código).
- `outside_event`: cuántos commits quedan antes de `evento.inicio` (`before_start`) o después de `evento.fin` (`after_end`) y la lista de esos commits. Un commit cuenta si su fecha de autor **o** su fecha de commit cae fuera; así se detecta código previo aunque se haya rebasado durante el evento.
- `has_git: false` si la carpeta o el ZIP no traen una carpeta `.git` propia (un archivo `.git` que apunta a otra ruta no se sigue).

Si `inicio`/`fin` no tienen zona horaria se toman en la hora local del revisor. No guarda mensajes, nombres ni correos de los autores. Si un equipo falla (por ejemplo, aún no se obtuvo), se reporta y se sigue con los demás (código de salida 1).

`acr scc` corre `scc --format json` sobre `repos/<id>` (solo cuenta líneas, no ejecuta el código) y escribe `reports/<id>/scc.json` con:

- `languages`: un elemento por lenguaje, del que tiene más líneas de código al que tiene menos, con `name`, `files`, `lines`, `code`, `comment`, `blank`, `bytes` y `complexity`.
- `totals`: número de lenguajes y la suma de los mismos campos para todo el repositorio.

Requiere `scc` en el `PATH` (PB-1). Se aplican las exclusiones por defecto de scc (`.git` y lo que indique `.gitignore`). Si un equipo falla (por ejemplo, aún no se obtuvo), se reporta y se sigue con los demás (código de salida 1); un `scc.json` anterior solo se reemplaza si scc terminó bien.

`acr megalinter` corre el contenedor de MegaLinter v10 (`docker.io/oxsecurity/megalinter:v10`, cambiable con `--image`) una vez por equipo, con `podman` o, si no está, `docker` (`--engine` para elegir):

- `repos/<id>` se monta **en solo lectura** como espacio de trabajo; los linters solo leen el código y nunca lo ejecutan ni lo corrigen (`APPLY_FIXES: none`).
- Usa siempre el [`.mega-linter.yml`](.mega-linter.yml) de este proyecto, montado en solo lectura (un `.mega-linter.yml` del equipo se ignora): linters del lenguaje detectado automáticamente, Semgrep (OWASP Top 10 y CWE Top 25, sin métricas), Betterleaks con `--redact` y Trivy; desactiva Gitleaks, TruffleHog, Secretlint, OSV-Scanner y Grype, que duplican a los anteriores. Sin reportes que publiquen fuera de la máquina; el envío a Loki/Prometheus locales llega con PB-16.
- Los reportes se escriben fuera del repositorio, en `reports/<id>/`: `megalinter.sarif`, `megalinter.json`, `megalinter.md`, `trivy.sarif` y `semgrep.sarif` (si esos linters corrieron) y la salida completa en `megalinter/`.

Que los linters encuentren errores no es un fallo. Si el contenedor falla, no deja sus reportes o pasa del tiempo límite (`--timeout-min`, 60 por defecto; el contenedor se detiene), se conservan los reportes anteriores y se sigue con los demás equipos (código de salida 1). Requiere `podman` o `docker` y la imagen (PB-1); Semgrep y Trivy descargan sus reglas y su base de CVE, pero no envían el código.

`acr scanoss` busca código copiado de proyectos open source conocidos en dos pasos, para que **solo salgan huellas** de la máquina:

1. `scanoss-py fingerprint` calcula en local (sin red) la huella WFP de `repos/<id>`: por archivo, su MD5, tamaño, ruta relativa y hashes de sus líneas.
2. Se quitan los archivos dentro de carpetas de dependencias (`node_modules`, `bower_components`, `vendor`, `third_party`, `venv`/`.venv`, `site-packages`, `Pods`, `Carthage`, `.gradle`, entre otras) y se revisa que cada línea restante sea un hash; si aparece cualquier otra cosa no se envía nada.
3. `scanoss-py scan --wfp` envía solo esa huella filtrada a la API gratuita de SCANOSS (osskb.org); ese proceso no recibe la ruta del repositorio.

Ambos pasos corren en una carpeta temporal vacía, así que un `scanoss.json` de configuración dentro del repositorio del equipo no se usa. Escribe `reports/<id>/scanoss.json` con:

- `totals`: archivos enviados (`files_scanned`), archivos con coincidencia (`files_matched`) y su porcentaje (`matched_percent`), y coincidencias de archivo completo y de fragmento.
- `excluded`: cuántos archivos de dependencias se excluyeron y en qué carpetas.
- `matches`: por archivo, tipo (`file` o `snippet`), porcentaje, líneas, componente (proyecto), proveedor, versión, `purl`, licencias y enlaces (`url`, `file_url`).

Una coincidencia indica similitud, no copia confirmada. Requiere `scanoss-py` en el `PATH` (PB-1). Si un equipo falla (por ejemplo, se alcanzó el límite de consultas gratuitas), se reporta, se conserva el `scanoss.json` anterior y se sigue con los demás (código de salida 1).

`acr dolos` compara el código de todos los equipos entre sí con Dolos, en una sola corrida sobre todos los repositorios obtenidos:

- Agrupa los archivos de `repos/<id>` por lenguaje según su extensión y corre Dolos una vez por cada lenguaje que usen al menos dos equipos. Deja fuera `.git` y las mismas carpetas de dependencias que SCANOSS. Los archivos de lenguajes que Dolos no soporta no se comparan y se cuentan por extensión.
- Dolos solo lee los archivos, que recibe en un CSV de entrada (`filename`, `label` = id del equipo). Corre con `--output-format csv`: no levanta su servidor web y nada sale de la máquina.
- Los pares de archivos del mismo equipo se ignoran.

Escribe en `reports/dolos/`:

- `pairs.csv`: una fila por cada par de equipos (todos, para la matriz), con `team_a`, `team_b`, `similarity` (la mayor similitud entre dos de sus archivos, de 0 a 1 como en Dolos), `file_pairs` (pares de archivos similares), `longest_fragment`, `total_overlap` y los dos archivos más parecidos (`file_a`, `file_b`).
- `file-pairs.csv`: cada par de archivos de equipos distintos que reporta Dolos, con su lenguaje y similitud.
- `dolos.json`: equipos y lenguajes comparados, archivos por equipo y archivos excluidos.

Una similitud alta indica parecido, no copia confirmada. Requiere Dolos en el `PATH` (PB-1) y al menos dos equipos obtenidos; los equipos sin obtener se avisan y no se comparan (código de salida 1). Si Dolos falla, se conservan los reportes anteriores.

`acr analyze` solo está registrado: responde "todavía no implementado" (código de salida 3) hasta que se complete su ítem del backlog.

Desarrollo: `python -m ruff check .` y `python -m pytest -q`.

## Privacidad

El código revisado pertenece a los equipos participantes:

- No se ejecuta ni se modifica.
- Las carpetas `repos/` y `reports/` están en `.gitignore` y nunca deben subirse a este repositorio.
- El dashboard corre solo en local; se comparte mediante reportes exportados (PDF, Markdown y ZIP).

## Licencia

[MIT](LICENSE)
