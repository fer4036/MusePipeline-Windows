# Arquitectura y plan de migración del pipeline Muse a Windows y cloud

## 1. Propósito del documento

Este documento describe la arquitectura objetivo de la rama
`feature/windows-cloud-agent`. Su finalidad es permitir que el desarrollo y las
pruebas continúen desde una computadora Windows sin depender del contexto de la
implementación original en Linux.

El objetivo no es trasladar ROS 2 ni BlueZ a Windows. El objetivo es conservar
las funciones del sistema de investigación mediante componentes equivalentes:

- un agente Python local controla el hardware Bluetooth y las diademas;
- la adquisición y los archivos sensibles permanecen en la computadora del
  investigador;
- una GUI hospedada en la nube permite controlar la sesión desde cualquier
  navegador con autorización;
- el agente y la nube se comunican mediante un WebSocket seguro saliente;
- ROS 2 permanece como integración opcional para trabajo futuro con robots.

## 2. Objetivo general del pipeline

El pipeline registra señales de varias diademas Muse S Athena durante
experimentos de colaboración humano-robot. Cada diadema se asocia con un
operador estable (`operador_a`, `operador_b`, etc.) y produce:

- EEG de cuatro electrodos;
- IMU: acelerómetro y giroscopio;
- datos ópticos de la Muse, almacenados bajo la modalidad PPG;
- nivel de batería cuando el dispositivo lo proporciona;
- eventos de conexión, desconexión y reconexión;
- métricas de recepción, publicación, colas y pérdida de datos.

La persona investigadora decide cuándo comienza y termina el registro. Que una
diadema esté conectada no significa que sus muestras deban guardarse de
inmediato.

Durante el taller, cada participante también responde cuatro afirmaciones de
engagement cognitivo mediante una escala Likert de 1 a 5. La medición puede
repetirse cada diez minutos y siempre debe quedar asociada con:

- sesión experimental;
- operador;
- sección del taller;
- número de medición;
- timestamp compatible con las señales fisiológicas.

## 3. Arquitectura objetivo

```text
┌─────────────────────────────────────────────────────────────┐
│ Navegadores                                                 │
│                                                             │
│ Investigador                       Participantes             │
│ control, estados y exportación     escala Likert             │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTPS / WSS
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Servicio cloud                                              │
│                                                             │
│ FastAPI + GUI web                                           │
│ autenticación por roles                                     │
│ registro de agentes conectados                              │
│ enrutamiento de comandos y acknowledgements                 │
│ estado operativo, sin EEG crudo                             │
└──────────────────────────┬──────────────────────────────────┘
                           │ WSS saliente iniciado localmente
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Agente local Windows                                        │
│                                                             │
│ SessionManager                                              │
│ descubrimiento y adquisición BrainFlow/Bleak                │
│ identidad operador-dispositivo                              │
│ reconexión y métricas                                       │
│ control de grabación                                        │
│ SQLite local y exportación CSV                              │
└──────────────────────────┬──────────────────────────────────┘
                           │ Bluetooth Low Energy
                           ▼
                  Muse S Athena 1 ... N
```

### 3.1 Principio local-first

El archivo SQLite es la fuente de verdad durante la sesión. Los CSV se generan
a partir de esa base al finalizar o exportar. SQLite se conserva porque aporta
transacciones, escritura por lotes, recuperación ante interrupciones y consultas
de validación que un CSV abierto no ofrece.

Por defecto no se transmiten muestras EEG, IMU o PPG a la nube. Solamente se
envían estados operativos, tasas, batería y confirmaciones de comandos. Las
respuestas Likert viajan por el gateway, pero su destino canónico es la base
local del agente.

### 3.2 Agente local

`muse_web.edge_agent` es el proceso que debe permanecer ejecutándose en la
computadora Windows del investigador. Sus responsabilidades son:

- abrir el WebSocket hacia el servicio cloud;
- autenticarse mediante `agent_id` y token;
- recibir comandos permitidos;
- crear y controlar sesiones locales;
- lanzar el colector Python;
- publicar estados del pipeline y de las diademas;
- mantener los datos fisiológicos fuera del servidor cloud.

El agente acepta actualmente los comandos:

- `prepare_pipeline`;
- `start_recording`;
- `stop_recording`;
- `stop_session`;
- `status`;
- `start_section`;
- `finish_section`;
- `submit_ground_truth`.

### 3.3 Adquisición en Windows

El backend seleccionado automáticamente en Windows es BrainFlow. Se utiliza
`MUSE_S_ATHENA_BOARD` con el preset `p1041`, que proporciona los flujos
necesarios para EEG, IMU, óptica y batería.

Bleak/WinRT se utiliza para descubrir los dispositivos anunciados. La
identificación considera nombre Muse, UUID de servicio y prefijo conocido de
dirección BLE.

Windows no expone los adaptadores como `hci1`, `hci2`, etc. Los valores `hci0`
que aparecen en algunos comandos de compatibilidad expresan la cantidad máxima
de dispositivos; no garantizan una asignación física diadema-adaptador. Windows
decide qué radio utiliza para cada conexión.

### 3.4 Backend Linux conservado

En Linux, `auto` continúa seleccionando `athena-linux`, el backend basado en
BlueZ y adaptadores HCI. El trabajo de Windows no debe eliminar:

- `bluetoothctl` y BlueZ para Linux;
- asignación explícita de adaptadores HCI;
- nodos y mensajes ROS 2;
- launch files existentes.

ROS 2 seguirá siendo opcional para integrar posteriormente el estado cognitivo
con un controlador de colaboración humano-robot.

### 3.5 Protocolo WebSocket

Los mensajes usan JSON y una versión explícita de protocolo. Cada mensaje
incluye:

```json
{
  "protocol_version": 1,
  "type": "status",
  "message_id": "uuid",
  "agent_id": "lab-windows-01",
  "session_id": "sesion-opcional",
  "timestamp": 1787520000.0,
  "payload": {}
}
```

El transporte incorpora:

- conexión saliente `ws://` o `wss://`;
- autenticación Bearer;
- heartbeat;
- ping/pong;
- reconexión con backoff y jitter;
- cola acotada de mensajes;
- `command_ack` ligado al ID del comando;
- lista explícita de acciones permitidas.

En producción siempre se debe utilizar `wss://`.

## 4. Funciones que deben conservarse

| Función | Pipeline actual | Objetivo Windows/cloud | Estado |
|---|---|---|---|
| Descubrimiento automático | BlueZ | Bleak/WinRT | Implementado, falta prueba física |
| Muse S Athena | Adaptador Athena propio | BrainFlow `MUSE_S_ATHENA_BOARD` | Implementado, falta prueba física |
| EEG de cuatro canales | Sí | Mismo contrato `EegSample` | Implementado |
| Acelerómetro y giroscopio | Sí | Mismo contrato `ImuSample` y unidades SI | Implementado |
| 16 canales ópticos | Sí | Mismo contrato `PpgSample` | Implementado |
| Batería | Cuando está disponible | Leer canal de batería BrainFlow | Implementado, falta validar |
| Identidad operador-diádema | MAC por sesión | Identidad estable por dispositivo | Implementado en memoria de sesión |
| Incorporación a media sesión | Sí | Scan periódico WinRT | Implementado, falta validar |
| Reconexión individual | Sí | Reintento coordinado y presencia previa | Implementado, falta validar |
| Conectar sin grabar | Sí | `prepare_pipeline` mantiene almacenamiento pausado | Implementado |
| Inicio/detención manual | Sí | Comandos cloud con ACK | Implementado |
| SQLite local | Sí | Fuente canónica local | Implementado |
| CSV por operador | Sí | Exportación al cerrar sesión | Implementado |
| CSV Muse solamente | Sí | Perfil sin Likert | Implementado |
| CSV Muse + Likert | Sí | Perfil combinado | Implementado |
| Métricas Hz | Sí | Estado enviado al cloud | Implementado |
| Estados y tiempo conectado | Sí | Tarjetas de la GUI cloud | Implementación inicial |
| Ground truth por sección | Sí | Comandos browser-cloud-agente | Implementación inicial |
| Medición cada 10 minutos | Sí | Conservar contador, sección y timestamps | Lógica local existente; falta aviso cloud completo |
| Preview de base | GUI local | Consulta remota sin exponer SQL | Pendiente |
| Tail de logs | GUI local | Tail filtrado a través del agente | Pendiente |
| Descarga de CSV | GUI local | Transferencia explícita y cifrada | Pendiente |
| Operación sin internet | Adquisición local | Seguir grabando y resincronizar GUI | Parcial |
| Cola offline de Likert | No completa | PWA + IndexedDB | Pendiente |
| Instalador Windows | No | Ejecutable/servicio firmado | Pendiente |
| ROS 2 opcional | Sí en Linux | Adaptador futuro, no requisito de adquisición | Conservado |

## 5. Reglas de identidad y concurrencia

La asignación operador-dispositivo no debe cambiar después de una desconexión.
Para lograrlo:

1. El registro crea el operador cuando observa por primera vez un identificador
   de diadema.
2. Una reconexión consulta el registro existente antes de crear un operador.
3. Los intentos de conexión se serializan para que un enlace nuevo no derribe
   conexiones activas.
4. Desconectar una diadema no debe liberar ni reiniciar las sesiones de las
   demás.
5. Los contadores de reconexión se guardan por operador.

En Windows será obligatorio verificar si una sola pila BLE puede mantener
cuatro Athena simultáneas. Si no es estable, las alternativas son:

1. probar el backend Bleak/WinRT con el decodificador Athena existente;
2. utilizar radios que Windows administre de manera independiente, si el
   controlador y sus drivers lo permiten;
3. utilizar un gateway Linux externo o mini-PC únicamente para adquisición;
4. distribuir la adquisición entre dos agentes y unificar las sesiones por
   timestamps.

## 6. Seguridad y datos sensibles

- Nunca guardar tokens en Git.
- Configurar secretos mediante variables de entorno.
- Usar tokens diferentes para investigador, participante y agente.
- Usar HTTPS/WSS en internet.
- No enviar EEG crudo al cloud por defecto.
- Usar códigos seudonimizados para participantes.
- Mantener SQLite y CSV en el perfil local del investigador.
- Registrar comandos, errores de conexión y cambios de grabación.
- En una versión productiva, sustituir tokens largos compartidos por
  credenciales de dispositivo y sesiones de corta duración.

Los patrones `.env`, bases SQLite, CSV y llaves ya están excluidos mediante
`.gitignore`.

## 7. Plan de trabajo

### Fase 1. Portabilidad estructural — completada

- Separar modelos Python de mensajes ROS 2.
- Mantener SQLite y CSV independientes de ROS.
- Crear selección de backend por plataforma.
- Mantener el backend Linux sin cambios funcionales.

### Fase 2. Backend Windows — implementada, pendiente de hardware

- Descubrir Athena con Bleak/WinRT.
- Adquirir con BrainFlow.
- Mapear los flujos a `EegSample`, `ImuSample` y `PpgSample`.
- Leer batería.
- Probar conexión, timeout y liberación de sesión.

Pruebas físicas requeridas:

1. una Muse durante 30 minutos;
2. dos Muse durante una hora;
3. cuatro Muse durante dos horas;
4. incorporación de dispositivos a media sesión;
5. desconexión y reconexión individual;
6. apagar una diadema sin afectar las demás;
7. confirmar que operador-MAC nunca cambia;
8. comparar tasas, canales y timestamps con una sesión Linux.

### Fase 3. Agente y control cloud — implementación inicial completada

- Protocolo JSON versionado.
- WebSocket saliente.
- Heartbeat, reconexión y ACK.
- Control de sesión y grabación.
- Publicación de estados sin señales crudas.
- Rutas separadas para investigador y participante.

### Fase 4. Paridad completa de GUI — siguiente fase

- Migrar preview local de SQLite mediante respuestas limitadas.
- Migrar tail de logs con filtrado y límites de tamaño.
- Mostrar Hz de EEG, IMU y PPG por operador.
- Mostrar batería, tiempo conectado y reconexiones.
- Mostrar temporizador y aviso de Likert cada diez minutos.
- Mantener aislada la pantalla del participante.
- Añadir cambio controlado de sección.
- Añadir descarga de CSV solicitada por el investigador.

### Fase 5. Resiliencia offline

- Mantener adquisición y grabación aunque se pierda internet.
- Reanudar WebSocket sin duplicar comandos.
- Convertir la página del participante en PWA.
- Guardar respuestas pendientes en IndexedDB.
- Sincronizar respuestas al recuperar conexión.
- Definir comportamiento ante cierre del navegador con respuestas pendientes.

### Fase 6. Persistencia y producción cloud

- Sustituir el registro en memoria por un almacén compartido.
- Persistir cuentas, agentes, auditoría y metadatos mínimos.
- Mantener los datos fisiológicos fuera del cloud.
- Probar reinicios y despliegues durante una sesión.
- Añadir límites de tasa y rotación de credenciales.

### Fase 7. Empaquetado Windows

- Crear ejecutable inicial con PyInstaller.
- Evaluar Nuitka para dificultar la extracción del código.
- Crear instalador MSIX o equivalente.
- Ejecutar el agente al iniciar sesión o como servicio Windows.
- Firmar instalador y ejecutable.
- Incluir diagnóstico de Bluetooth, permisos, firewall y versiones.

### Fase 8. Integración humano-robot

- Conservar un adaptador ROS 2 opcional en Linux.
- Definir una interfaz de estado cognitivo independiente del transporte.
- Permitir que un robot consuma estado procesado, no necesariamente EEG crudo.
- Validar sincronización entre eventos del robot, señales y ground truth.

## 8. Preparación de la computadora Windows

### 8.1 Software requerido

Instalar:

- Git para Windows;
- Python 3.11 o 3.12 de 64 bits;
- un navegador actualizado;
- drivers de los adaptadores Bluetooth, si Windows no los instala
  automáticamente.

Durante la instalación de Python se recomienda activar **Add Python to PATH**.
Los siguientes comandos están escritos para PowerShell.

### 8.2 Clonar la rama

```powershell
cd $env:USERPROFILE
git clone --branch feature/windows-cloud-agent --single-branch https://github.com/fer4036/MusePipeline-ROS2.git muse
cd .\muse
git branch --show-current
```

La última instrucción debe mostrar:

```text
feature/windows-cloud-agent
```

Si el repositorio ya existe:

```powershell
cd $env:USERPROFILE\muse
git fetch origin
git switch feature/windows-cloud-agent
git pull --ff-only origin feature/windows-cloud-agent
```

### 8.3 Crear el entorno Python

```powershell
cd $env:USERPROFILE\muse
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
pip install -r .\requirements-windows.txt
pip install -e .\muse_hrc
pip install -e .\muse_web
```

Cada nueva terminal PowerShell debe activar el entorno:

```powershell
cd $env:USERPROFILE\muse
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 9. Comandos para correr directamente en Windows

### 9.1 Prueba de una sola diadema con MAC conocida

Encender solamente una Athena y ejecutar:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\MuseResearch" | Out-Null

python -m muse_hrc.standalone `
  --device "operador_a,00:55:DA:XX:XX:XX,windows" `
  --acquisition-backend brainflow `
  --db "$env:USERPROFILE\MuseResearch\prueba_una_muse.sqlite"
```

Reemplazar `00:55:DA:XX:XX:XX` únicamente en esta prueba diagnóstica. La
operación habitual utilizará descubrimiento automático.

Detener con `Ctrl+C`.

### 9.2 Descubrimiento automático de hasta cuatro diademas

```powershell
python -m muse_hrc.standalone `
  --hci-devices "hci0,hci1,hci2,hci3" `
  --acquisition-backend brainflow `
  --max-devices 4 `
  --scan-seconds 12 `
  --scan-interval 15 `
  --db "$env:USERPROFILE\MuseResearch\prueba_cuatro_muse.sqlite"
```

En Windows `hci0...hci3` son marcadores de compatibilidad y limitan la cantidad
de dispositivos. No corresponden necesariamente a cuatro radios físicos.

### 9.3 Conectar sin registrar inmediatamente

```powershell
python -m muse_hrc.standalone `
  --hci-devices "hci0,hci1,hci2,hci3" `
  --acquisition-backend brainflow `
  --max-devices 4 `
  --paused `
  --control-file "$env:USERPROFILE\MuseResearch\control.json" `
  --db "$env:USERPROFILE\MuseResearch\prueba_pausada.sqlite"
```

Esta modalidad está pensada para ser controlada por `SessionManager` o por el
agente, no para editar manualmente el archivo de control.

### 9.4 GUI local existente

Para probar primero sin cloud:

```powershell
$env:MUSE_WEB_HOST = "127.0.0.1"
$env:MUSE_WEB_PORT = "8765"
python -m muse_web.app
```

Abrir en la misma computadora:

```text
http://127.0.0.1:8765
```

En la interfaz se debe seleccionar el backend Python/standalone. En Windows,
el colector seleccionará BrainFlow automáticamente.

### 9.5 Ejecutar el agente conectado al cloud

El servicio cloud debe estar desplegado mediante `Dockerfile.cloud` o
`render.yaml`. Configurar el token del agente solamente en la terminal:

```powershell
$env:MUSE_AGENT_TOKEN = "TOKEN_SECRETO_DEL_AGENTE"

python -m muse_web.edge_agent `
  --cloud-url "wss://TU-SERVICIO.onrender.com/ws/agent/lab-windows-01" `
  --agent-id "lab-windows-01" `
  --max-devices 4 `
  --sessions-root "$env:USERPROFILE\MuseResearch\sessions"
```

Mantener esta terminal abierta durante el experimento. La computadora puede
estar detrás de NAT o firewall porque la conexión WebSocket se inicia desde el
agente hacia internet.

Liga del investigador:

```text
https://TU-SERVICIO.onrender.com/?role=pipeline&token=TOKEN_INVESTIGADOR
```

Liga de participante:

```text
https://TU-SERVICIO.onrender.com/?role=operator&agent=lab-windows-01&token=TOKEN_PARTICIPANTE
```

## 10. Ubicación de los datos en Windows

Si se usa `--sessions-root` como en el ejemplo, cada sesión queda debajo de:

```text
C:\Users\USUARIO\MuseResearch\sessions\
```

Cada carpeta de sesión puede contener:

- `raw.sqlite`: base local canónica;
- `metadata.json`: configuración y estado de sesión;
- `pipeline.log`: eventos y métricas;
- `control.json` y `control_ack.json`: control local de grabación;
- CSV por operador y perfil de exportación.

No subir esta carpeta a Git ni sincronizarla automáticamente con servicios
personales de nube sin autorización del protocolo de investigación.

## 11. Diagnóstico inicial en Windows

Comprobar versiones:

```powershell
python --version
python -c "import platform; print(platform.platform())"
python -c "import brainflow; print('BrainFlow disponible')"
python -c "import bleak; print('Bleak disponible')"
python -m muse_hrc.standalone --help
python -m muse_web.edge_agent --help
```

Ver procesos Python:

```powershell
Get-Process python -ErrorAction SilentlyContinue
```

Ver los archivos recientes:

```powershell
Get-ChildItem "$env:USERPROFILE\MuseResearch" -Recurse |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 30 FullName, Length, LastWriteTime
```

Consultar conteos SQLite requiere una herramienta como `sqlite3.exe` o un
script Python. Ejemplo sin instalar SQLite CLI:

```powershell
python -c "import sqlite3; p=r'$env:USERPROFILE\MuseResearch\prueba_cuatro_muse.sqlite'; c=sqlite3.connect(p); print({t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('eeg_logs','imu_logs','ppg_logs')})"
```

Si PowerShell no expande correctamente la ruta dentro de ese comando, usar:

```powershell
$env:MUSE_TEST_DB = "$env:USERPROFILE\MuseResearch\prueba_cuatro_muse.sqlite"
python -c "import os,sqlite3; c=sqlite3.connect(os.environ['MUSE_TEST_DB']); print({t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('eeg_logs','imu_logs','ppg_logs')})"
```

## 12. Criterios para considerar completa la migración

La migración no debe declararse terminada solamente porque la GUI abra. Debe
cumplir:

- cuatro Muse conectadas y transmitiendo durante dos horas;
- EEG cercano a 256 muestras por segundo antes de efectos de visualización;
- IMU cercana a 52 Hz;
- óptica cercana a 64 Hz;
- batería visible cuando Athena la envíe;
- ninguna reasignación operador-dispositivo;
- una desconexión no interrumpe otras diademas;
- incorporación y reconexión a media sesión;
- pérdida de internet no detiene el archivo local;
- reconexión cloud no duplica comandos;
- ground truth asociado a operador, sección y timestamp correctos;
- CSV local por operador disponible al finalizar;
- paridad de columnas, unidades y timestamps respecto a Linux;
- ninguna muestra fisiológica enviada al cloud sin habilitación explícita.

## 13. Archivos principales para continuar el desarrollo

- `muse_hrc/muse_hrc/backends.py`: selección por plataforma.
- `muse_hrc/muse_hrc/brainflow_acquisition.py`: adquisición Windows.
- `muse_hrc/muse_hrc/windows_discovery.py`: descubrimiento WinRT.
- `muse_hrc/muse_hrc/cloud_protocol.py`: contrato JSON.
- `muse_hrc/muse_hrc/cloud_transport.py`: cliente WebSocket.
- `muse_hrc/muse_hrc/standalone.py`: colector Python.
- `muse_web/muse_web/edge_agent.py`: agente local permanente.
- `muse_web/muse_web/cloud_app.py`: gateway FastAPI.
- `muse_web/muse_web/cloud_static/`: GUI cloud inicial.
- `muse_web/muse_web/session_manager.py`: sesiones y control de registro.
- `requirements-windows.txt`: dependencias del agente Windows.
- `requirements-cloud.txt`: dependencias del contenedor cloud.
- `Dockerfile.cloud` y `render.yaml`: despliegue cloud.

## 14. Pruebas automatizadas antes de cada push

Desde Linux:

```bash
PYTHONPATH=muse_hrc:muse_web web_env/bin/python -m pytest -q \
  muse_hrc/test/test_auto_discovery.py \
  muse_hrc/test/test_windows_backend.py \
  muse_hrc/test/test_brainflow_acquisition.py \
  muse_hrc/test/test_cloud_protocol.py \
  muse_web/test/test_session_manager.py \
  muse_web/test/test_edge_agent.py
```

Desde PowerShell con el entorno activo:

```powershell
$env:PYTHONPATH = "muse_hrc;muse_web"
python -m pytest -q `
  muse_hrc/test/test_windows_backend.py `
  muse_hrc/test/test_brainflow_acquisition.py `
  muse_hrc/test/test_cloud_protocol.py `
  muse_web/test/test_session_manager.py `
  muse_web/test/test_edge_agent.py
```

Las pruebas automatizadas validan contratos y lógica, pero no sustituyen las
pruebas físicas BLE.
