# habitica-mcp

Servidor [MCP](https://modelcontextprotocol.io) que permite a LLMs (Claude, Cursor, VS Code Copilot,
modelos locales...) leer y gestionar tus tareas de [Habitica](https://habitica.com): hábitos,
diarias, pendientes, recompensas, checklists y etiquetas. Escrito en Python con
[FastMCP](https://gofastmcp.com).

[Read in English](README.md)

> Estado: **alfa (v0.1)**. Sin afiliación con HabitRPG, Inc.

## ¿Por qué otro servidor MCP para Habitica?

Antes de empezar revisamos los existentes (septiembre de 2026). El más popular dejó de mantenerse en
2025 y ya no funciona porque no envía el header `x-client` que Habitica exige; las alternativas en
Python eran experimentos de un solo archivo. Este servidor apunta a ser **correcto, seguro y
mantenible**:

- **Respeta las reglas de la API de Habitica**: siempre envía `x-client`, se mantiene bajo el límite
  de 30 peticiones por minuto (y obedece `Retry-After` / `X-RateLimit-*`) y nunca ejecuta el cambio
  de día (cron).
- **Seguro por defecto**: borrar, quitar ítems de checklist o etiquetas y comprar recompensas
  devuelven una vista previa hasta que el modelo repite la llamada con `confirm=true`. El modo
  `read-only` oculta todas las herramientas de escritura.
- **Pensado para LLMs**: pocas herramientas orientadas a flujos, referencias por id *o* por texto
  ("marca meditar como hecha"), respuestas compactas sin el historial pesado de Habitica y errores
  que dicen qué hacer.
- **Probado y medido**: un Habitica falso en memoria, snapshot del contrato de herramientas, pruebas
  contra la API real y evals con LLMs (ver [docs/measurement.md](docs/measurement.md)).

## Herramientas

| Herramienta | Qué hace | Modifica Habitica |
|---|---|---|
| `habitica_get_overview` | Stats, diarias pendientes hoy, pendientes vencidos/de hoy/próximos, hábitos, avisos | no |
| `habitica_list_tasks` | Filtra por tipo, estado (pendiente, de hoy, vencida, completada), etiqueta, texto | no |
| `habitica_get_task` | Detalle completo de una tarea (checklist, horario, fechas) | no |
| `habitica_list_tags` | Etiquetas y cuántas tareas las usan | no |
| `habitica_create_task` | Crea hábito, diaria (con horario), pendiente (fecha, checklist) o recompensa | sí |
| `habitica_update_task` | Cambia texto, notas, dificultad, fecha, horario, etiquetas... | sí |
| `habitica_score_task` | Marca/desmarca diarias y pendientes, +/- en hábitos, compra recompensas (`confirm`) | sí |
| `habitica_manage_checklist` | Agrega, renombra, marca o quita (`confirm`) ítems de checklist | sí |
| `habitica_manage_tags` | Crea, renombra o borra (`confirm`) etiquetas | sí |
| `habitica_delete_task` | Borra una tarea (`confirm`) | sí |

No se exponen a propósito: cron, gemas y pagos, reinicio de cuenta, chat y mensajes de party.

## Inicio rápido

1. Instala [uv](https://docs.astral.sh/uv/).
2. En Habitica ve a **Configuración > Datos del sitio** y copia tu **User ID** y tu **API Token**.
3. Verifica la conexión:

   ```bash
   HABITICA_USER_ID=... HABITICA_API_TOKEN=... uvx habitica-mcp --check
   ```

   Hasta la primera versión en PyPI, reemplaza `uvx habitica-mcp` por
   `uvx --from git+https://github.com/CDMonsalveA/habitica-mcp habitica-mcp`.

Las configuraciones para Claude Desktop, Cursor, Claude Code y VS Code están en el
[README en inglés](README.md#claude-desktop--cursor). Después pregunta cosas como
*"¿Qué me falta hoy en Habitica?"*, *"Agrega un pendiente para llamar al dentista el viernes,
dificultad difícil, con checklist: buscar el número, preguntar por limpieza"* o
*"Ya medité, márcalo"*.

## Configuración

| Variable | Por defecto | Descripción |
|---|---|---|
| `HABITICA_USER_ID` | — | Tu User ID de Habitica (necesario para llamar a Habitica) |
| `HABITICA_API_TOKEN` | — | Tu API Token de Habitica (necesario, nunca se registra en logs) |
| `HABITICA_MCP_MODE` | `standard` | `read-only` expone solo las cuatro herramientas de lectura |
| `HABITICA_BASE_URL` | `https://habitica.com` | Otra instancia de Habitica (autoalojada o de pruebas) |
| `HABITICA_X_CLIENT` | `<id del mantenedor>-habitica-mcp` | Cambia el header `x-client` (para forks) |
| `HABITICA_MCP_RATE_LIMIT` | `25` | Máximo de peticiones por minuto (Habitica permite 30) |
| `HABITICA_MCP_MAX_WAIT` | `30` | Segundos que una herramienta puede esperar un turno libre antes de fallar |
| `HABITICA_MCP_CACHE_TTL` | `15` | Segundos que se reutilizan las lecturas (`0` desactiva) |
| `HABITICA_MCP_TIMEOUT` | `20` | Timeout por petición en segundos |
| `HABITICA_MCP_LOG_LEVEL` | `WARNING` | Logs a stderr; `INFO` agrega una línea de métricas por llamada |
| `HABITICA_MCP_METRICS_FILE` | — | Guarda métricas por llamada (sin contenido de tareas) en JSON lines |

Tus credenciales no salen de tu equipo: el servidor corre localmente por stdio y solo habla con Habitica.

## Desarrollo

```bash
uv sync
uv run pre-commit install    # verificaciones antes de cada commit (ver CONTRIBUTING.md)
uv run pytest
uv run ruff check . && uv run pyright
uv run python -m tests.fake_habitica --port 8765
```

Decisiones: [docs/adr](docs/adr) · Medición: [docs/measurement.md](docs/measurement.md) ·
Mantenimiento: [docs/maintenance.md](docs/maintenance.md) · [CONTRIBUTING.md](CONTRIBUTING.md) ·
[SECURITY.md](SECURITY.md)

## Licencia

[MIT](LICENSE)
