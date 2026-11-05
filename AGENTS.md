# Guía para agentes de IA

Este repositorio implementa **Advanced Code Review**: un framework de revisión estática de repositorios para hackathons. Lee esto antes de escribir código.

## Orden de lectura

1. `README.md`: resumen.
2. `SPEC.md`: **fuente de verdad** de qué construir.
3. `MOSCOW.md`: prioridad y fase de cada ítem (`PB-n`). Construye solo lo que pide el ítem.

## Invariantes que no se negocian

1. **Nunca ejecutar el código de los equipos.** Solo se obtiene y se pasa a herramientas de análisis estático.
2. **Nada sale de la máquina** salvo lo que la spec permite (huellas de SCANOSS). Ni código de equipos ni reportes se suben a ningún servicio.
3. **El código y los reportes de los equipos nunca entran al repositorio**: `repos/`, `reports/`, `entregas/` y `equipos.yml` están en `.gitignore`.
4. **Costo cero:** solo herramientas open source o gratuitas.
5. **Solo `localhost`:** ningún servicio escucha fuera de la máquina ni permite acceso anónimo externo.
6. **Secretos detectados** nunca se muestran en claro en dashboards ni reportes.

## Al escribir código

- Python 3.12, paquete `acr` en `acr/`, tests en `tests/`. Estilo y errores con `ruff`.
- Las dependencias están fijadas en `requirements-dev.txt` y solo una persona las cambia. Si hace falta una nueva, no la agregues: pídela.
- Las herramientas externas (`git`, `scc`, `podman`/`docker`, `scanoss-py`, `dolos`, `md-to-pdf`) se invocan con `subprocess`, con la lista de argumentos (nunca `shell=True`), y en los tests se simulan: los tests no usan red ni requieren esas herramientas instaladas.
- Los nombres de carpetas salen de `equipos.yml`: valida `id` (minúsculas, números y guiones) antes de usarlo en una ruta.
- Cita el ítem (`PB-n`) y la sección de `SPEC.md` en commits y PRs.
- La spec se escribe en español; identificadores, nombres de campos y código en inglés.

## Al cambiar la spec

`SPEC.md` y `MOSCOW.md` los cambia una persona. Si un requisito parece incorrecto o falta información, no lo inventes: dilo.
