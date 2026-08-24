# MusePipeline Windows + Cloud Deployment

## Arquitectura

La GUI del investigador y del operador corre en Render como servicio FastAPI publico.

La laptop Windows corre localmente el EdgeAgent. Ese agente se conecta por WebSocket seguro a Render y controla el pipeline Bluetooth/BrainFlow de la diadema Muse.

Flujo:

Investigador -> Render Cloud GUI -> WebSocket -> EdgeAgent Windows -> Muse por Bluetooth

Operador/participante -> Render Cloud GUI -> WebSocket -> EdgeAgent Windows -> SQLite local

## Render

Tipo de servicio: Web Service

Build Command:

pip install -r requirements.txt

Start Command:

python -m muse_web.cloud_app

Variables de entorno:

MUSE_CLOUD_RESEARCHER_TOKEN=dev-researcher
MUSE_CLOUD_OPERATOR_TOKEN=dev-operator
MUSE_CLOUD_AGENT_TOKENS={"lab-windows-01":"clave-agente-01"}

Health check path:

/health

URL investigador:

https://TU-SERVICIO.onrender.com/?role=pipeline&token=dev-researcher

URL operador:

https://TU-SERVICIO.onrender.com/?role=operator&token=dev-operator

## Windows EdgeAgent

En la laptop con la Muse:

python -m muse_web.edge_agent `
  --cloud-url "wss://TU-SERVICIO.onrender.com/ws/agent/lab-windows-01" `
  --agent-id "lab-windows-01" `
  --agent-token "clave-agente-01" `
  --max-devices 1 `
  --sessions-root "$env:USERPROFILE\MuseResearch\sessions"

## Bluetooth en Windows

Arquitectura recomendada:

1 laptop Windows + 1 adaptador Bluetooth Intel activo + 1 diadema Muse + 1 EdgeAgent

No se recomienda por ahora conectar varias Muse a la misma laptop Windows usando varios adaptadores USB Bluetooth.

## Verificacion

Probar Render:

Invoke-WebRequest -UseBasicParsing https://TU-SERVICIO.onrender.com/health | Select-Object -ExpandProperty Content

Respuesta esperada:

{"ok":true,"agents":0,"configured_agent_ids":["lab-windows-01"],"configuration_error":""}

## Problemas comunes

Si la pagina dice "Clave de investigador invalida":

- Verifica que la URL tenga ?role=pipeline&token=TU_TOKEN_REAL
- El token debe coincidir exactamente con MUSE_CLOUD_RESEARCHER_TOKEN en Render.

Si el EdgeAgent marca HTTP 403:

- El agent-token no coincide.
- El agent-id no existe en MUSE_CLOUD_AGENT_TOKENS.
- MUSE_CLOUD_AGENT_TOKENS no es JSON valido.

Formato correcto:

{"lab-windows-01":"clave-agente-01"}

Si marca HTTP 404:

- La URL del WebSocket apunta al servicio equivocado.
- Usa el dominio real de Render con wss://.

Si marca HTTP 500:

- Revisa /health.
- Probablemente MUSE_CLOUD_AGENT_TOKENS esta mal formado.
