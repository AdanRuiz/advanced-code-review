# Agente de desarrollo sin supervisión

Al iniciar sesión en la laptop, un agente de Claude Code toma los issues de GitHub de este repositorio y los resuelve por fases ([MOSCOW.md](../MOSCOW.md)), y entrega todo como PRs. Usa la suscripción de Claude Code del mantenedor, sin API de pago. Está adaptado del agente del proyecto smartcity.

## Qué hace en cada corrida

Corre 3 minutos después de iniciar sesión y luego cada hora mientras la sesión siga abierta. Una corrida sin nada que hacer solo consulta git y GitHub: no gasta cuota de Claude ni comenta en la bitácora.

1. Espera la red y actualiza un clon aislado en `%LOCALAPPDATA%\acr-agent\repo`. Prepara un entorno de Python propio (`%LOCALAPPDATA%\acr-agent\venv`) con `requirements-dev.txt`.
2. **Bugs:** toma hasta 3 issues abiertos con la etiqueta `agent`, del más antiguo al más nuevo, y salta los que ya tienen una rama `agent/issue-<n>`.
3. **Backlog:** si no hay un PR `agent/backlog-*` abierto, implementa un ítem:
   - Lee `**Fase actual:**` y `**Adelanto permitido:**` de `MOSCOW.md` en `main`.
   - Toma el issue `backlog` de la fase actual (etiqueta `fase-N`) por prioridad (`must` → `should` → `could`) y número; si no queda ninguno, sigue con la fase de adelanto. Nunca toma `humano` ni `agent:bloqueado`.
   - El PR lleva `Closes #n`. Al mezclarlo, la siguiente corrida (a más tardar en una hora) arranca el siguiente ítem.
   - Si el ítem no se puede hacer, lo comenta y le pone `agent:bloqueado`. Si no cabe en una sesión, responde `PARTIR:` y el script crea ítems más chicos y cierra el original.
4. Por cada tarea abre un PR contra `main`. Si `ruff` o `pytest` fallan, el PR se abre como borrador.
5. **Errores pasajeros** (cuota, límite de tasa, red, sesión vencida): la tarea queda como *Reintento*, el issue no se toca y la siguiente corrida lo reintenta.

**Nada se mezcla ni se despliega sin una persona.** El mantenedor revisa y mezcla cada PR.

**Avanzar de fase:** cuando una fase esté lista, edita `MOSCOW.md` (`**Fase actual:**` y `**Adelanto permitido:**`) y haz push. El agente no puede cambiar ese archivo.

## Límites de seguridad

| Riesgo | Contención |
|---|---|
| El modelo empuja código o abre PRs por su cuenta | Solo edita y hace commit. El clon tiene el push deshabilitado y `git push` y `gh` están denegados. El push y el PR los hace `run.ps1`, que se lee del checkout del mantenedor, no del clon |
| Se debilita sus propias reglas o cambia la spec | Un PR que toca `.github/`, `scripts/agent/`, `SPEC.md`, `MOSCOW.md`, `requirements*.txt`, lockfiles, `equipos.yml`, `.env` o `LICENSE` no se abre |
| Mete dependencias (cadena de suministro) | `pip`, `uv`, `npm`, `npx`, `pnpm`, `curl` y `wget` están denegados; las dependencias las fija una persona en `requirements-dev.txt` |
| Ejecuta código de los equipos o contenedores | `podman` y `docker` están denegados; los tests simulan las herramientas externas |
| Un issue intenta manipularlo (inyección de prompt) | Solo toma issues con `agent` o `backlog`, etiquetas que solo ponen colaboradores. El prompt trata el texto del issue como datos |
| Cobra a la API en vez de usar la suscripción | `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` y `ANTHROPIC_BASE_URL` se borran del entorno antes de lanzar `claude` |
| Se acaba la cuota | Máximo 3 bugs y 1 ítem por corrida, un solo PR del backlog abierto a la vez y 45 minutos por tarea |
| El modelo cambia sin avisar | Fijado en `run.ps1 -Model` (hoy `claude-opus-5-5`); queda registrado en cada PR y en la bitácora |
| El agente se degrada y sigue gastando cuota | Freno automático (ver "Métricas") |

Todo lo que no está en la lista de permitidos (`$ClaudeSettings` en `run.ps1`) queda denegado, porque en modo `-p` no hay a quién preguntar. Los permisos son una defensa más, no un aislamiento completo: la revisión humana de cada PR es la protección principal.

## Instalación

Una sola vez, en PowerShell, desde la raíz del repositorio:

```powershell
gh auth status                     # cuenta con permiso de push al repo
claude                             # comprobar que la sesión de Claude Code está iniciada; salir con /exit
powershell -ExecutionPolicy Bypass -File scripts\agent\run.ps1 -DryRun   # simulacro: elige tareas, no llama a Claude ni escribe en GitHub
powershell -ExecutionPolicy Bypass -File scripts\agent\install.ps1       # registra la tarea al iniciar sesión
```

**¿Por qué al iniciar sesión y no al encender el equipo?** Claude Code y `gh` guardan su sesión en tu perfil de usuario. Una tarea que corre antes de que alguien inicie sesión necesitaría guardar tu contraseña de Windows en la tarea. Con la tarea al iniciar sesión, encender el equipo y entrar a Windows basta para que arranque.

Para lanzarla en el momento: `Start-ScheduledTask -TaskName advanced-code-review-agent`. Para cambiar la frecuencia: `install.ps1 -EveryMinutes 30`. Para quitarla: `install.ps1 -Uninstall`.

## Uso diario

- **Delegar un bug:** crea el issue con pasos para reproducirlo y ponle la etiqueta `agent`.
- **Agregar un ítem al backlog:** issue con título `PB-n · <título>` y etiquetas `backlog`, `fase-N` y `must`/`should`/`could`; agrega `humano` si lo hace una persona.
- **Revisar:** los PRs llegan con un resumen y `Closes #n`. Mezclar uno cierra su issue.
- **Desbloquear:** en un issue `agent:bloqueado`, responde lo que pidió el agente y quita la etiqueta. Para reintentar un ítem cuyo PR cerraste sin mezclar, borra también la rama `agent/backlog-<n>`.
- **Pausar desde GitHub** (también desde el celular): abre un issue con la etiqueta `agent:pausa`. Mientras siga abierto, el agente no empieza ninguna tarea. Para reanudar, cierra el issue.
- **Pausar en la laptop:** `Disable-ScheduledTask -TaskName advanced-code-review-agent`; para reanudar, `Enable-ScheduledTask`.
- **Bitácora:** el issue fijado con la etiqueta `agent:bitacora` recibe un comentario por cada corrida que ejecutó una tarea o falló, y su cuerpo muestra el avance por fase y las métricas. GitHub no te notifica de tu propia actividad, así que esos comentarios no generan avisos.
- **Freno automático:** si se juntan 3 tareas seguidas bloqueadas, 3 PRs seguidos cerrados sin mezclar o 3 corridas seguidas fallidas, o si una sesión tiene 15 denegaciones o más, `metrics.ps1` abre un issue "Pausa automática del agente" con `agent:pausa`. Revisa la causa y cierra el issue para reanudar.
- **Logs locales:** `%LOCALAPPDATA%\acr-agent\logs` (se borran a los 30 días).
- **Ajustes:** `run.ps1 -MaxIssues 1`, `-SkipBacklog`, `-TaskTimeoutMinutes 20` y `-Model <id>`. Para usarlos en la tarea programada, edita sus argumentos en `install.ps1`.

## Límites conocidos

- Solo corre con la laptop encendida y la sesión iniciada.
- Consume la misma cuota que tus sesiones interactivas de Claude Code.
- No da los pasos humanos (issues con `humano`): instalar herramientas, hablar con los organizadores, aprobar dependencias.
- Las verificaciones solo cubren Python (`ruff` y `pytest`). Cuando empiece la fase 4 (Next.js), hay que agregar las de la app web en `Test-Checks` de `run.ps1`.
