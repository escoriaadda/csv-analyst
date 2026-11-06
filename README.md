# csv-analyst

Agente conversacional de análisis de datos CSV con el [Mistral Agents SDK](https://docs.mistral.ai/studio/agents/agents-sdk). El agente está en `main.py`.

## Qué hace

- Recibe un enlace a un CSV en Google Drive y lo descarga con el conector `google_drive_mcp` (actúa como el usuario de la sesión).
- Carga el CSV con la herramienta fija `load_csv` y lo asocia a la sesión (memoria por sesión vía `ToolCallbackContext.session.id`).
- Responde preguntas en lenguaje natural usando solo las herramientas fijas `describe_columns`, `filter_rows` y `group_aggregate`, con cifras reales calculadas sobre los datos.
- No tiene sandbox: no ejecuta código arbitrario.
- La sesión la almacena Mistral al desplegar, así que las preguntas de seguimiento recuerdan el archivo y la conversación.

## Despliegue gestionado en Mistral Cloud

El proyecto se despliega desde este repositorio de GitHub con el despliegue gestionado de Mistral (equivalente a `agents deploy`): Mistral compila el Dockerfile del commit pusheado y ejecuta el worker. Como el agente usa el conector de Google Drive como el usuario de la sesión (OBO), el despliegue queda endurecido (`hardened`).

## Interactuar con el agente desplegado

- Desde Vibe (terminal): `uv run --env-file .env agents chat csv-analyst` abre una sesión persistente; para seguir una conversación anterior, reconecta por su `session_id` con `agents.Agent.load_session(session_id)`.
- Desde tu app: `agent = agents.Agent.load_from(agent_name="csv-analyst")`, luego `async with await agent.create_session() as session:` y `session.run_turn(...)` para conservar la memoria entre mensajes.
- Al desplegar, Mistral imprime un enlace para chatear con el agente en Vibe Code Web.

## Desarrollo local

```bash
uv run --env-file .env agents info     # muestra lo que carga el CLI
uv run --env-file .env agents chat     # chat en tu proceso (sesión en memoria)
```

`MISTRAL_API_KEY` va en `.env` (git-ignored), con una key de [API keys](https://console.mistral.ai/api-keys).
