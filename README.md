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
6. Entregar el dashboard y un reporte por equipo en Markdown y PDF, más un ZIP con todos.

## Uso

Requiere Python 3.12 y las dependencias de `requirements-dev.txt`.

```sh
python -m acr --help                 # lista los comandos
python -m acr validate               # valida equipos.yml (PB-5)
python -m acr fetch [ID]             # obtiene los repositorios en repos/<id> (PB-6)
python -m acr git-signals [ID]       # señales del historial de git (PB-7, PB-57)
python -m acr scc [ID]               # lenguajes, líneas y archivos con scc (PB-8)
python -m acr megalinter [ID]        # calidad, Semgrep, Betterleaks y Trivy con MegaLinter (PB-9)
python -m acr scanoss [ID]           # código open source conocido con SCANOSS, solo huellas (PB-10)
python -m acr dolos                  # similitud de código entre todos los equipos con Dolos (PB-11)
python -m acr analyze [ID]           # corre el análisis completo (PB-12)
python -m acr security               # datos de la página Seguridad en reports/security.json (PB-21)
python -m acr plagiarism             # datos de la página Plagio en reports/plagiarism.json (PB-22)
python -m acr review-prompt ID [-o ARCHIVO]  # prompt de Claude Code para la revisión asistida (PB-13)
python -m acr review-validate [ID]   # valida review.json y web-plagiarism.json (PB-13)
python -m acr report [ID]            # reporte en inglés en reports/<id>/reporte.md y reports/judge-questions.md (PB-14, PB-59)
python -m acr export [ID]            # reporte.md y reporte.pdf por equipo y reports/todos.zip (PB-23)
python -m acr serve [--port 8000]    # API del orquestador sobre equipos.yml, solo en 127.0.0.1 (PB-24)
python -m acr --config otro.yml ...  # usa otro archivo de entrada
```

`acr validate` revisa `equipos.yml` y lista todos los errores a la vez (campos faltantes, `id` inválidos o repetidos, fechas mal formadas o `fin` anterior a `inicio`); sale con código 0 si el archivo es válido y 1 si no.

También valida los campos del checklist cuando están presentes (PB-56):

- `integrantes`: lista con `nombre` y `github` de cada integrante. El usuario de GitHub va sin `@` ni URL y no puede repetirse.
- `presentacion`: URL `http(s)://` o ruta a un PDF local.
- `prompts_ia`: ruta a un PDF local.

Cada PDF local debe existir y empezar con la firma `%PDF-`. Las rutas relativas se resuelven desde la carpeta de `equipos.yml`. Si a un equipo le falta alguno de estos campos, `acr validate` muestra un aviso con el punto del checklist que quedará en revisión manual (1.2, 4.1 o 3.4). Los avisos no cambian el código de salida.

`acr fetch` deja cada equipo en `repos/<id>` según su `repo`:

- **URL git** (`https://`, `http://`, `ssh://`, `git://` o `usuario@host:ruta`): `git clone` con historial completo, en la rama `rama` si se indica y fijado en `commit` si se indica. Usa las credenciales de git del revisor; los enlaces simbólicos se obtienen como archivos normales. Otros esquemas (`file://`, `ext::`) se rechazan.
- **Carpeta local:** se copia sin seguir enlaces simbólicos.
- **ZIP** (`.zip`): se descomprime rechazando rutas peligrosas (*zip slip*) y enlaces simbólicos, con un límite de tamaño descomprimido (`--max-zip-mb`, 1024 MB por defecto) y de 100 000 entradas.

`rama` y `commit` solo aplican a URL git. Cada equipo se obtiene en una carpeta temporal y reemplaza a `repos/<id>` solo si todo salió bien; los archivos quedan en solo lectura. Si un equipo falla, se reporta y se sigue con los demás (código de salida 1). Las rutas relativas se toman desde el directorio actual. El código obtenido nunca se ejecuta.

`acr git-signals` lee el historial de `repos/<id>` (solo `git rev-parse` y `git log` sobre la versión obtenida, sin ejecutar el código) y escribe `reports/<id>/git-signals.json` con:

- `first_commit` y `last_commit` (hash, fecha de autor y fecha de commit), `commit_count` y `single_commit` (un solo commit con todo el código).
- `outside_event`: cuántos commits quedan antes de `evento.inicio` (`before_start`) o después de `evento.fin` (`after_end`) y la lista de esos commits. Un commit cuenta si su fecha de autor **o** su fecha de commit cae fuera; así se detecta código previo aunque se haya rebasado durante el evento.
- `has_git: false` si la carpeta o el ZIP no traen una carpeta `.git` propia (un archivo `.git` que apunta a otra ruta no se sigue).
- Punto 1.1 (PB-57): `commits`, del más antiguo al más nuevo, con autor, archivos y líneas agregadas, borradas y cambiadas de cada commit; los totales `lines_added` y `lines_deleted`; y `largest_commit` / `largest_commit_percent`, el commit con más líneas agregadas y qué % de todas las líneas agregadas representa. Las líneas en carpetas de dependencias (`node_modules/`, `vendor/`…) y en *lockfiles* no cuentan como código del equipo: se reportan aparte en `excluded_lines`. Los binarios y los commits de *merge* no suman líneas.
- Punto 1.2 (PB-57): `authors` con nombre, usuario de GitHub (si se conoce), si es integrante, commits y líneas de cada uno; `author_count` y `single_author` (**alerta**: las reglas piden 2 o más integrantes). Si el equipo tiene `integrantes` en `equipos.yml`, `members` da los commits de cada integrante, `members_without_commits` los que no tienen ninguno y `authors_not_members` cuántos autores no se reconocen. Un autor se reconoce como integrante por su usuario de GitHub (correo `…@users.noreply.github.com` o nombre de autor igual al usuario) o por su nombre completo, sin distinguir mayúsculas ni acentos.
- `fork: null` (sin dato): un clon no dice si el repositorio es fork; saberlo requiere consultar la API de GitHub, una salida de red que la spec aún no contempla.

Si `inicio`/`fin` no tienen zona horaria se toman en la hora local del revisor. No guarda mensajes ni correos de los autores; los correos solo se usan para reconocer a los integrantes. Si un equipo falla (por ejemplo, aún no se obtuvo), se reporta y se sigue con los demás (código de salida 1).

`acr scc` corre `scc --format json` sobre `repos/<id>` (solo cuenta líneas, no ejecuta el código) y escribe `reports/<id>/scc.json` con:

- `languages`: un elemento por lenguaje, del que tiene más líneas de código al que tiene menos, con `name`, `files`, `lines`, `code`, `comment`, `blank`, `bytes` y `complexity`.
- `totals`: número de lenguajes y la suma de los mismos campos para todo el repositorio.

Requiere `scc` en el `PATH` (PB-1). Se aplican las exclusiones por defecto de scc (`.git` y lo que indique `.gitignore`). Si un equipo falla (por ejemplo, aún no se obtuvo), se reporta y se sigue con los demás (código de salida 1); un `scc.json` anterior solo se reemplaza si scc terminó bien.

`acr megalinter` corre el contenedor de MegaLinter v10 (`ghcr.io/oxsecurity/megalinter:v10`, cambiable con `--image`; Docker Hub solo llega a v9) una vez por equipo, con `podman` o, si no está, `docker` (`--engine` para elegir):

- Se monta una **copia desechable** de `repos/<id>` (en la carpeta temporal del sistema) como espacio de trabajo, porque MegaLinter v10 escribe archivos temporales ahí; `repos/<id>` nunca se monta ni se modifica y la copia se borra al terminar. Los enlaces simbólicos se copian como enlaces, sin seguirlos. Los linters nunca corrigen el código (`APPLY_FIXES: none`).
- Usa siempre el [`.mega-linter.yml`](.mega-linter.yml) de este proyecto, montado en solo lectura (un `.mega-linter.yml` del equipo se ignora): linters del lenguaje detectado automáticamente, Semgrep (OWASP Top 10 y CWE Top 25, sin métricas), Betterleaks con `--redact` y Trivy; desactiva Gitleaks, TruffleHog, Secretlint, Kingfisher, DevSkim, OSV-Scanner y Grype, que duplican a los anteriores, y lychee y Dustilock, que salen a internet con datos del repositorio. Sin reportes que publiquen fuera de la máquina; el envío a Loki/Prometheus locales llega con PB-16.
- Los reportes se escriben fuera del repositorio, en `reports/<id>/`: `megalinter.sarif`, `megalinter.json`, `megalinter.md`, `trivy.sarif` y `semgrep.sarif` (si esos linters corrieron) y la salida completa en `megalinter/`.
- El contenedor corre endurecido (PB-40): `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--pids-limit=4096` y `--memory=8g` (sin swap adicional). Del equipo anfitrión solo se montan la copia desechable del repositorio, la configuración (solo lectura) y la carpeta temporal de reportes; nada más (ni `-v`, ni el socket del motor, ni `--privileged`).

**Riesgo residual:** algunos linters cargan archivos de configuración del repositorio del equipo que son código (por ejemplo `eslint.config.js`, `prettier.config.js` o `load-plugins` de pylint). Eso ejecuta código del equipo dentro del contenedor, con acceso a la red, aunque `repos/<id>` no se modifique; contradice en parte el invariante 1 de `AGENTS.md`. El endurecimiento limita el impacto (sin capacidades, sin escalar privilegios, sin otros archivos del anfitrión, con límites de procesos y memoria), pero no lo elimina. Desactivar la red del contenedor o los linters que cargan configuración ejecutable es una decisión pendiente de los organizadores (cambio de `SPEC.md`).

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
- Corre en un contenedor de un solo uso (PB-60) con la imagen oficial `ghcr.io/dodona-edu/dolos-cli:2.9.0` (versión fija, cambiable con `--image`), con `podman` o, si no está, `docker` (`--engine` para elegir). Así no hace falta instalar Dolos con npm, que en Windows exige compilar `@dodona/dolos-parsers` con Visual Studio Build Tools.
- El contenedor no tiene red (`--network=none`) y tiene el mismo endurecimiento que MegaLinter (PB-40: `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--pids-limit=4096`, `--memory=8g`). Del anfitrión solo se montan `repos/` en **solo lectura** (en `/repos`) y una carpeta temporal dentro de `reports/dolos/` (en `/work`) con el CSV de entrada y la salida cruda de Dolos, que se borra al terminar. Las rutas del CSV de entrada son las del contenedor (`/repos/<id>/...`) y las de la salida de Dolos se traducen de vuelta al equipo y su ruta relativa. Con Docker en Linux o macOS corre con el usuario actual (`--user uid:gid`) para poder escribir en esa carpeta.
- Los pares de archivos del mismo equipo se ignoran.

Escribe en `reports/dolos/`:

- `pairs.csv`: una fila por cada par de equipos (todos, para la matriz), con `team_a`, `team_b`, `similarity` (la mayor similitud entre dos de sus archivos, de 0 a 1 como en Dolos), `file_pairs` (pares de archivos similares), `longest_fragment`, `total_overlap` y los dos archivos más parecidos (`file_a`, `file_b`).
- `file-pairs.csv`: cada par de archivos de equipos distintos que reporta Dolos, con su lenguaje y similitud.
- `dolos.json`: equipos y lenguajes comparados, archivos por equipo y archivos excluidos.

Una similitud alta indica parecido, no copia confirmada. Requiere `podman` o `docker` en el `PATH` y la imagen de Dolos (la primera corrida la descarga, ~400 MB; sin red se puede bajar antes con `docker pull`), y al menos dos equipos obtenidos; sin motor de contenedores falla con un error claro. Los equipos sin obtener se avisan y no se comparan (código de salida 1). Si Dolos falla o pasa de 30 minutos por lenguaje (el contenedor se detiene), se conservan los reportes anteriores. En `acr analyze`, `--engine` aplica también a Dolos y `--dolos-image` cambia su imagen.

`acr analyze` corre el análisis completo, en el orden de la spec, para todos los equipos o solo para `ID`:

1. Por equipo: `fetch`, `git-signals`, `scc`, `megalinter` y `scanoss` (mismas opciones `--max-zip-mb`, `--engine`, `--image` y `--timeout-min`). Si falla la obtención, ese equipo se detiene ahí; si falla un paso posterior, se registra y se siguen los demás pasos y equipos.
2. Al final, `dolos` una sola vez sobre todos los equipos obtenidos (también los que no se volvieron a analizar), para que la matriz quede al día. Con menos de dos equipos obtenidos se omite.
3. Después, `security` y `plagiarism` sobre todos los equipos de `equipos.yml` (ver abajo).
4. Al final, `export` sobre todos los equipos: las exportaciones (Markdown, PDF y ZIP) se regeneran en la misma corrida.

El avance queda en `reports/status.json`, que se reescribe en cada paso:

- `teams`: un elemento por equipo de `equipos.yml` con `state` (`pendiente`, `obteniendo`, `analizando`, `listo` o `error`), el paso en curso (`step`), el resultado de cada paso (`steps`: `ok`, `error`, `omitido`), `started_at`/`finished_at`, `error` y `log` (`<id>/analyze.log`, relativo a `reports/`, con la bitácora de la última corrida).
- `dolos`: estado, equipos comparados y hora de la última corrida de Dolos.

Un análisis a la vez por equipo: cada equipo se bloquea con un candado del sistema operativo en `reports/.locks/`; si ya hay un análisis en curso para un equipo, no se lanza otro y se avisa. Si el proceso muere, el candado se libera solo y el equipo que quedó "analizando" pasa a `error` ("análisis interrumpido") en la siguiente escritura. Sale con código 1 si algún equipo terminó con error, estaba ocupado, Dolos falló o no se pudo exportar algún reporte. La revisión asistida con Claude Code (PB-13) no forma parte del pipeline.

`acr security` (también al final de `acr analyze`) resume en `reports/security.json` los datos de la página Seguridad del dashboard para todos los equipos de `equipos.yml`, a partir de los SARIF que ya están en `reports/<id>/` (no vuelve a analizar). Infinity lee una URL por consulta y no puede recorrer los equipos elegidos en la variable `equipo`, por eso los paneles leen este único archivo y filtran sus filas por equipo:

- `teams`: por equipo, CVE por severidad CVSS (crítica, alta, media, baja, desconocida), **CVE críticas/altas con versión corregida** (indicador principal), total de CVE, hallazgos de Semgrep y secretos, y si había `trivy.sarif`, `semgrep.sarif` y `megalinter.sarif`.
- `vulnerabilities` (`trivy.sarif`): CVE, paquete, versión instalada y corregida, CVSS, severidad y si cuenta en el indicador principal; una fila por CVE, paquete y versión.
- `weaknesses` y `insecure_patterns` (`semgrep.sarif`): hallazgos contados por CWE y por categoría OWASP Top 10 (`sin clasificar` si la regla no trae etiqueta), y cada hallazgo con regla, CWE, OWASP, severidad, archivo y línea.
- `secrets` (Betterleaks en `megalinter.sarif`): **solo tipo, archivo y línea**; ni el valor ni el mensaje del hallazgo se copian.

Un SARIF que falta o no se puede leer deja ese equipo en cero y se avisa. Usa el mismo análisis de SARIF que `acr report`, así que el reporte y el dashboard cuentan lo mismo.

`acr plagiarism` (también al final de `acr analyze`) hace lo mismo para la página Plagio: junta en `reports/plagiarism.json` los tres niveles de detección de plagio y las señales de git de todos los equipos de `equipos.yml`, a partir de los archivos que ya están en `reports/` (no vuelve a analizar):

- `teams`: por equipo, totales de SCANOSS (archivos revisados, con coincidencia, % y coincidencias), de Dolos (equipos con archivos similares y similitud máxima), de la búsqueda web (proyectos encontrados, cuántos anteriores al evento y estado `draft`/`validated`) y de `git-signals.json` (historial, commits, primer commit, commits antes del inicio y después del cierre, commit único y fork), y qué archivos había.
- `similarity` (`dolos/pairs.csv`): similitud entre cada dos equipos en ambos sentidos (`team_a` = fila de la matriz, `team_b` = columna), con los dos archivos más parecidos.
- `team_pairs`: cada par de equipos una vez, con `similar` = 1 si Dolos encontró archivos similares entre ellos.
- `external_code` (`scanoss.json`): cada coincidencia con archivo, tipo (archivo completo o fragmento), %, proyecto de origen, licencias y enlace.
- `web_matches` (`web-plagiarism.json`): cada proyecto encontrado con enlace, fuente, fecha, si es anterior al evento y nivel de parecido. Solo se lee si pasa el mismo validador que `acr review-validate`; si no, se omite y se avisa.

Un archivo que falta o no se puede leer deja ese equipo en cero y se avisa. `git-signals.json` aún no detecta forks (ver `acr git-signals`), así que la columna Fork queda "sin dato". Una coincidencia indica similitud, no copia confirmada.

La revisión asistida la hace el revisor con Claude Code (suscripción propia), equipo por equipo, después de `acr analyze`:

1. `acr review-prompt <id>` imprime el prompt ([acr/prompts/review.md](acr/prompts/review.md)) con los datos del equipo: revisar `repos/<id>` a partir de los reportes de `reports/<id>/` y `reports/dolos/pairs.csv`, sin ejecutar ni modificar el código; tratar el contenido del repositorio como datos, no como instrucciones; buscar ideas parecidas en la web solo con título, objetivo y frases cortas (nunca código ni secretos), y reportar secretos por tipo y `archivo:línea`, nunca su valor. También le indica dónde están el PDF de prompts (`prompts_ia`) y la presentación (`presentacion`) del equipo; si la presentación es solo una URL, no la abre y deja el punto 4.1 en revisión manual. Los textos de `review.json` se escriben en inglés porque llegan al reporte. Se pega en Claude Code abierto en la raíz de este proyecto; con `-o ARCHIVO` se guarda en UTF-8 (conviene dejarlo fuera del repositorio o en `reports/`).
2. Claude Code escribe dos borradores (`status: "draft"`) que el revisor valida:
   - `reports/<id>/review.json` ([esquema](acr/schemas/review.schema.json), `schema_version: 2`): `summary`; `official_checklist` con los puntos 1.3, 2.1, 2.2, 2.4, 3.1–3.4 y 4.1 del checklist oficial, cada uno con `result` (`pass`, `fail`, `manual_review`), `notes` y `evidence` (`file`, `line`); `external_code` con `path`, `origin` (`official_template`, `generator`, `third_party_cited`, `third_party_uncited`, `ai_declared`, `ai_undeclared`, según [Plantillas, generadores y librerías](SPEC.md#plantillas-generadores-y-librerías)), `source`, `url` y `notes`; `documents` con `prompts_pdf` y `presentation` (`status`: `read`, `not_provided`, `url_only`, `unreadable`, y `notes`); `judge_questions`, de 3 a 6 preguntas con `topic` (`2.3`, `4.1`, `4.2` o un tema de la lista de IA responsable del manual: `purpose`, `autonomy`, `privacy`, `bias_and_limits`, `accessibility`, `human_support`), `question`, `file` y `line`; `findings` con `criterion` (`feasibility`, `quality`, `security`, `responsible_ai`, `plagiarism`), `severity` (`critical` a `info`), `file`, `line`, `title`, `description` y, opcionales, `recommendation`, `source`, `cwe` y `owasp_llm`; `ai_checklist` con `uses_ai`, `providers` y los 8 puntos de la checklist de IA responsable, y `owasp_llm` con los 5 riesgos de OWASP Top 10 para LLM de la spec (`LLM01`, `LLM02`, `LLM05`, `LLM06`, `LLM10`). Cada punto lleva `status` (`pass`, `fail`, `not_applicable`, `unknown`), `notes` y `evidence` (`file`, `line`).
   - `reports/<id>/web-plagiarism.json` ([esquema](acr/schemas/web-plagiarism.schema.json)): `keywords`, `queries` (búsqueda y fuente) y `matches` con `title`, `url`, `source` (`github`, `devpost`, `hackathon_gallery`, `product_hunt`, `tutorial`, `other`), `published` (`AAAA-MM-DD` o `null`), `before_event`, `similarity` (`idea`, `structure`, `text`) y `evidence`.
3. `acr review-validate [<id>]` revisa ambos archivos contra su esquema JSON y además: cada punto del checklist oficial y de las checklists de IA exactamente una vez, `id` de hallazgos sin repetir, rutas relativas dentro del repositorio, `team_id` igual a la carpeta, fechas válidas (`generated_at` con zona horaria) y que **ningún campo contenga un secreto en claro** (claves privadas, claves de AWS o Google, tokens de GitHub o Slack, API keys `sk-…`, JWT); el mensaje de error nunca repite el valor. Lista todos los errores y sale con código 1 si falta un archivo o alguno es inválido.

`acr report` (PB-14, PB-59) arma `reports/<id>/reporte.md` **en inglés** con la plantilla de [Entregables](SPEC.md#entregables) a partir de los resultados que ya estén en `reports/` (no vuelve a analizar):

- **Summary:** lenguajes y tamaño (`scc.json`), objetivo, totales del checklist (*pass · fail · manual review*) y hallazgos clave (puntos que no cumplen, CVE críticas/altas con corrección, resumen de `review.json`, resultados que faltan). El encabezado enlaza al dashboard (`--dashboard-url`, `http://localhost:3000` por defecto).
- **Official checklist:** tabla con los 14 puntos (1.1 a 4.3) marcados *Pass*, *Fail* o *Manual review* con su evidencia (`archivo:línea`):
  - 1.1 (`git-signals.json`): un solo commit → *Fail*; el commit más grande con 50 % o más de las líneas agregadas → *Manual review* (carga masiva a explicar); si no, *Pass*.
  - 1.2 (`git-signals.json`): un solo autor → *Fail*; integrantes de `equipos.yml` sin commits → *Manual review*; si no, *Pass*. Lista autores y commits por autor.
  - 4.3 (Betterleaks en `megalinter.sarif`): secretos → *Fail*, **solo con tipo, archivo y línea, nunca el valor**; sin secretos → *Pass*; si Betterleaks no corrió → *Manual review*.
  - 2.3 y 4.2: siempre *Manual review* (requieren entrevista o demo).
  - 1.3, 2.1, 2.2, 2.4, 3.1–3.4 y 4.1: la marca y la evidencia de `review.json`; 3.4 y 4.1 agregan lo que dice del PDF de prompts y de la presentación. Sin `review.json` válido quedan en *Manual review*.
- **Plagiarism and originality evidence:** coincidencias de `scanoss.json` (con columna *Cited* según la clasificación de `external_code` de `review.json`), pares de `reports/dolos/pairs.csv`, proyectos de `web-plagiarism.json` (enlace, fecha, si es anterior al evento, nivel), historial de git (commits antes/después del sprint, commit único, autores, fork) y clasificación de plantillas, generadores y código de terceros.
- **Questions for the judges:** las de `review.json`, con tema y `archivo:línea`.
- **Reviewer feedback:** lo escribe el revisor; el reporte deja puntos borrador derivados de los hallazgos.
- **Technical annex (formative, not scored):** CVE de `trivy.sarif`, patrones inseguros de `semgrep.sarif` por CWE y OWASP Top 10, nota de calidad A–E de MegaLinter (fórmula de la página Calificación: linters sin hallazgos + 0.5 × linters solo con advertencias, sobre el total), checklist de IA responsable y OWASP LLM de `review.json`, y los demás hallazgos de la revisión asistida.

Además escribe `reports/judge-questions.md` con las preguntas para el jurado de **todos** los equipos de `equipos.yml`.

Toda marca automática es un borrador que valida el revisor. `review.json` y `web-plagiarism.json` solo se usan si pasan `acr review-validate`; si no, el reporte dice cuántos errores tienen. Si falta un resultado o no se puede leer, su sección lo dice y nombra el comando que lo genera. El texto que viene de los repositorios o de la revisión asistida (rutas, reglas, notas, enlaces) se escapa para que no inyecte Markdown ni HTML; solo se enlazan URL `http(s)`.

`acr export [ID]` (también al final de `acr analyze`) genera las descargas de los botones del dashboard (PB-23, [Exportación de reportes](SPEC.md#exportación-de-reportes)):

- `reports/<id>/reporte.md`: se regenera como en `acr report`.
- `reports/<id>/reporte.pdf`: el Markdown convertido con [md-to-pdf](https://github.com/simonhaenisch/md-to-pdf) (requiere md-to-pdf en el `PATH`, PB-1). md-to-pdf levanta un servidor HTTP local con su carpeta base mientras convierte; por eso corre sobre una copia del reporte en una carpeta temporal vacía y nunca ve `reports/` ni el código de los equipos.
- `reports/judge-questions.md`: se regenera como en `acr report`.
- `reports/todos.zip`: `reporte.md` y `reporte.pdf` de **todos** los equipos de `equipos.yml` (aunque se pase un `ID`), `judge-questions.md` y los CSV de Dolos (`dolos/pairs.csv`, `dolos/file-pairs.csv`). No incluye SARIF, bitácoras ni otros archivos de `reports/`; lo que falte se avisa.

Cada archivo se escribe aparte y reemplaza al anterior solo al terminar: si md-to-pdf falla o no está instalado, se conserva el PDF anterior, el Markdown y el ZIP se escriben igual y el comando sale con código 1.

### API del orquestador

`acr serve` levanta la API del orquestador (PB-24, [API del orquestador](SPEC.md#página-configuración-desde-el-dashboard)) en `http://127.0.0.1:8000`. Lee y escribe el mismo `equipos.yml` que se edita a mano:

| Método y ruta | Acción |
| --- | --- |
| `GET /api/evento`, `PUT /api/evento` | Leer y guardar `nombre`, `inicio` y `fin` |
| `GET /api/equipos` | Listar equipos |
| `POST /api/equipos`, `PUT /api/equipos/{id}`, `DELETE /api/equipos/{id}` | Alta, edición y baja |

- Cada cambio se valida con las mismas reglas de `acr validate` (incluidos `id` y PDF locales) antes de escribir; los errores vuelven con código 422 como `{"detail": [{"location", "message"}]}`. Solo se valida la parte editada, así que el archivo se puede llenar desde cero; `acr validate` sigue revisando el archivo completo.
- Un `id` repetido da 409 y no se puede cambiar el `id` de un equipo (se da de baja y se crea otro). Los campos desconocidos se rechazan y los `null` se omiten.
- Si `equipos.yml` no es YAML válido, la API responde 409 y nunca lo sobrescribe. Las escrituras son atómicas, pero **no conservan los comentarios** del archivo.
- Solo `localhost`: escucha únicamente en `127.0.0.1` (no hay opción `--host`) y responde 403 a peticiones que no vengan de una dirección de loopback o cuyo `Host` u `Origin` no sea `localhost` (protege contra *DNS rebinding* y contra otras páginas abiertas en el navegador). No publica `/docs` ni `/openapi.json`.
- La API nunca obtiene ni ejecuta el código de los equipos. Subir ZIP, validar origen y lanzar análisis llegan con PB-25 y PB-26.

### Dashboard local (servicios)

[`compose.yaml`](compose.yaml) levanta los servicios del dashboard (PB-15) con `podman compose up -d` (o `docker compose up -d`) desde la raíz del proyecto:

| Servicio | Imagen | Acceso |
| --- | --- | --- |
| Grafana | `grafana/grafana-oss` | `http://localhost:3000`, con usuario y contraseña |
| Servidor de archivos | `caddy` | `http://localhost:8080/reports/...`, `reports/` en solo lectura, con usuario y contraseña |
| Loki | `grafana/loki` | Sin puerto publicado: solo desde la red interna |
| Prometheus | `prom/prometheus` | Sin puerto publicado: solo desde la red interna |

Las contraseñas no tienen valor por defecto y no se guardan en el repositorio: si falta alguna, `compose` no arranca. Se definen como variables de entorno o en un archivo `.env` junto a `compose.yaml` (está en `.gitignore`):

```sh
ACR_GRAFANA_ADMIN_PASSWORD=...   # cuenta de administración de Grafana (usuario: ACR_GRAFANA_ADMIN_USER, admin por defecto)
ACR_FILES_PASSWORD=...           # servidor de archivos (usuario: ACR_FILES_USER, acr por defecto)
```

- **Solo `localhost`:** Grafana y el servidor de archivos publican su puerto solo en `127.0.0.1`. Loki y Prometheus viven en una red interna sin salida a internet y sin puertos hacia el anfitrión; solo los alcanzan Grafana y el servidor de archivos.
- **Sin acceso anónimo:** Grafana con acceso anónimo, registro de usuarios, dashboards públicos y autenticación básica de la API desactivados. El servidor de archivos pide usuario y contraseña (se guarda solo su hash en memoria), solo acepta `GET`/`HEAD`, no lista carpetas, no sirve archivos ocultos (como `reports/.locks/`) y responde con `Content-Security-Policy: sandbox` para que un HTML de un reporte no ejecute nada.
- **Nada sale de la máquina:** sin instantáneas externas de Grafana, sin estadísticas de uso, avisos de versión ni noticias de Grafana, y sin estadísticas de uso de Loki.
- Todos los contenedores corren sin capacidades (`cap_drop: ALL`) y con `no-new-privileges`; los datos de Grafana, Loki y Prometheus quedan en volúmenes con nombre.
- Crea `reports/` (o corre `acr analyze`) antes de levantar los servicios para que la carpeta montada sea la del revisor.

Grafana se configura sola al arrancar (PB-17), desde [`services/grafana/`](services/grafana/) montada en solo lectura:

- **Plugin Infinity** con versión fija (`GF_PLUGINS_PREINSTALL_SYNC`), sin actualización automática. Se descarga una vez al arrancar, como las imágenes.
- **Datasource Infinity** (`acr-infinity`): lee los JSON/CSV de `reports/` desde el servidor de archivos por la red interna (`http://files:8080`), con el mismo usuario y contraseña (`ACR_FILES_USER`, `ACR_FILES_PASSWORD`, que Grafana recibe por variable de entorno). Solo puede consultar ese servidor (`allowedHosts`). **Datasource Prometheus** (`acr-prometheus`) apunta al Prometheus interno.
- **6 páginas** en la carpeta *Advanced Code Review*: 1. Resumen (página de inicio), 2. Equipo, 3. Calidad, 4. Seguridad, 5. Plagio y 6. Calificación. Todas llevan la etiqueta `advanced-code-review` y un *dashboard link* por esa etiqueta, que las muestra como botones arriba (navbar) y conserva las variables y el rango de tiempo al cambiar de página. No se editan desde la interfaz: se cambian en los JSON.
- **Variable `equipo`** en todas las páginas: Todos / uno / varios, con los `id` de `reports/status.json` (lo escribe `acr analyze`; sin ese archivo la lista queda vacía).
- **Variable `herramienta`** solo en Calidad y Seguridad: linters, con `label_values(linter)` de Prometheus.
- **Botones de exportación** (PB-23) en todas las páginas, junto al navbar: **PDF equipo** y **MD equipo** (`http://localhost:8080/reports/${equipo}/reporte.pdf` y `.md`) y **ZIP todos** (`/reports/todos.zip`). Se abren en otra pestaña, piden el usuario y la contraseña del servidor de archivos y se descargan como archivo (`Content-Disposition: attachment`). Con `equipo` = Todos o varios equipos, el servidor de archivos redirige PDF y MD a `todos.zip`. Los archivos los genera `acr export` (o `acr analyze`).

**1. Resumen** (PB-41) tiene por ahora el panel **Estado del análisis**: tabla que lee `reports/status.json` con Infinity, filtrada por la variable `equipo`, con el id y el nombre de cada equipo, el estado coloreado (pendiente gris, obteniendo azul, analizando amarillo, listo verde, error rojo) y la hora de la última corrida (`finished_at`, «sin corrida» si nunca terminó). Clic en el equipo abre la página Equipo filtrada.

El panel **Alertas de plagio** (PB-42) lee `reports/plagiarism.json` (el mismo archivo de la página Plagio) con Infinity, filtrado por la variable `equipo`, y muestra solo los equipos con alguna coincidencia y cuántas: **SCANOSS** (coincidencias con código externo de `scanoss.json`), **Dolos** (otros equipos con archivos similares según `dolos/pairs.csv`) y **Web** (proyectos parecidos de `web-plagiarism.json`), en verde (0) o naranja (1 o más). Sin coincidencias dice «Sin alertas de plagio». Clic en el equipo abre la página Equipo filtrada; el detalle está en la página Plagio. Los demás paneles del Resumen llegan con PB-18 y PB-43.

**2. Equipo** (PB-46) tiene una fila por cada equipo elegido en la variable `equipo` (fila repetida de Grafana, así funciona también con Todos o varios equipos), con los paneles que salen de archivos de `reports/<id>/` leídos con Infinity:

- **Ficha:** tarjetas de líneas de código y número de archivos (`totals` de `scc.json`).
- **Lenguajes:** barras horizontales con las líneas de código por lenguaje (`languages` de `scc.json`).
- **Revisión asistida:** hallazgos de Claude Code de `review.json` con criterio, gravedad (coloreada de crítica a info), `archivo:línea` y descripción. «Sin hallazgos» si no hay.
- **Checklist de IA responsable:** los 8 puntos de `ai_checklist` de `review.json` con ✓ (verde), ✗ (rojo), no aplica o sin evidencia, y las notas.
- La nota A–E, el quality gate y los paneles de MegaLinter llegan con PB-47.

**4. Seguridad** (PB-21) lee `reports/security.json` con Infinity, filtrado por la variable `equipo`:

- 4 tarjetas verde (0) / rojo (1 o más): **CVE críticas/altas con corrección** (indicador principal, arriba a la izquierda), hallazgos de Semgrep, secretos de Betterleaks y total de CVE.
- **CVE por severidad:** barras apiladas por equipo (crítica, alta, media, baja).
- **Dependencias vulnerables:** tabla ordenada por CVSS con CVE, paquete, versiones instalada y corregida, CVSS y severidad.
- **Código inseguro por categoría:** barras por equipo agrupadas por CWE y por OWASP Top 10.
- **Patrones inseguros:** regla, CWE, OWASP, severidad, archivo y línea.
- **Secretos expuestos:** equipo, tipo, archivo y línea, sin el valor.
- Clic en un equipo en las tablas o en las barras de CVE abre la página Equipo filtrada.

**5. Plagio** (PB-22) lee `reports/plagiarism.json` con Infinity, filtrado por la variable `equipo`:

- 3 tarjetas verde (0) / naranja (1 o más): coincidencias con código externo (SCANOSS), pares de equipos similares (Dolos; cada par cuenta una vez) y proyectos parecidos en la web.
- **Similitud entre equipos:** matriz equipo × equipo de Dolos coloreada por % (verde → rojo); filas = equipos elegidos, columnas = todos.
- **Código externo:** barras con el % de archivos con coincidencia por equipo y tabla por archivo con tipo, %, proyecto de origen, licencias y enlace.
- **Ideas en la web:** equipo, proyecto encontrado, enlace, fuente, fecha, si es anterior al evento, nivel de parecido (idea / estructura / texto) y estado de la revisión.
- **Historial de git:** commits, primer commit, commits antes del inicio y después del cierre, commit único y fork.
- Clic en un equipo en las tablas, las barras o la matriz abre la página Equipo filtrada; los enlaces de SCANOSS y de la web abren en otra pestaña.

Calidad y Calificación aún no tienen paneles (llegan con PB-18 a PB-20), y el envío de MegaLinter a Loki/Prometheus llega con PB-16; hasta entonces la variable `herramienta` queda vacía. Los paneles de Seguridad salen de archivos, no de Prometheus, y todavía no se filtran por `herramienta`.

Desarrollo: `python -m ruff check .` y `python -m pytest -q`.

## Privacidad

El código revisado pertenece a los equipos participantes:

- No se ejecuta ni se modifica.
- Las carpetas `repos/` y `reports/` están en `.gitignore` y nunca deben subirse a este repositorio.
- El dashboard corre solo en local; se comparte mediante reportes exportados (PDF, Markdown y ZIP).

## Licencia

[MIT](LICENSE)
