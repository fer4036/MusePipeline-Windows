# Agente Muse para Windows

Esta carpeta documenta la primera versión portable del colector. El backend
Linux sigue disponible; en Windows, `--acquisition-backend auto` selecciona
BrainFlow y el descubrimiento utiliza Bleak/WinRT.

## Requisitos

- Windows 10 versión 2004 (build 19041) o Windows 11, de 64 bits.
- Python 3.11 o 3.12 de 64 bits.
- Bluetooth habilitado y diademas Muse S Athena cargadas.

## Entorno de desarrollo (PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-windows.txt
pip install -e .\muse_hrc
pip install -e .\muse_web
```

## Prueba local sin cloud

```powershell
python -m muse_hrc.standalone `
  --hci-devices hci0,hci1,hci2,hci3 `
  --acquisition-backend brainflow `
  --max-devices 4 `
  --db "$env:USERPROFILE\MuseResearch\test.sqlite"
```

Los nombres `hci0...hci3` solo expresan el máximo esperado para mantener
compatibilidad con la GUI actual. Windows decide qué radio Bluetooth utiliza;
no existe una asignación física por `hciN` como en BlueZ.

## Agente permanente con gateway cloud

```powershell
$env:MUSE_AGENT_TOKEN = "reemplazar"
python -m muse_web.edge_agent `
  --max-devices 4 `
  --cloud-url "wss://servidor.example/ws/agent/lab-01" `
  --agent-id "lab-01"
```

Este proceso permanece esperando órdenes. `prepare_pipeline` crea la sesión
local y lanza el colector BrainFlow; `start_recording`, `stop_recording` y
`stop_session` controlan el registro y la exportación sin enviar las señales
al servidor.

No se envían muestras EEG, IMU o PPG al gateway. Únicamente se transmiten
estados y métricas; SQLite y los CSV siguen siendo locales.
