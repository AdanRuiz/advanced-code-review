# Bitácora del agente

Issue fijado que mantiene `scripts/agent/run.ps1`: cada corrida que hace algo o falla deja aquí un comentario, y el cuerpo muestra el avance por fase y las métricas. Las corridas sin nada que hacer no comentan. Los comentarios los publica la cuenta de `gh`, así que esa cuenta no recibe avisos de ellos.

| Columna | Significado |
|---|---|
| Tarea | Bug (`#n` con `agent`) o ítem del backlog (`#n PB-n`) |
| Resultado | PR abierto, PR en borrador (verificaciones rojas), Bloqueado con el motivo, Reintento o Partido en ítems nuevos |
| Turnos, Min | Tamaño de la sesión de Claude: un indicador del consumo de cuota |
| Denegaciones | Comandos que los permisos del agente rechazaron. Un aumento brusco merece revisar el log |
| USD eq. API | Lo que costaría la sesión con la API. **No se cobra**: se usa la suscripción. Sirve para comparar tareas |

No cierres este issue: si se cierra, el agente abre uno nuevo. Guía: `docs/agente.md`.
