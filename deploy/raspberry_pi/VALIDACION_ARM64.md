# Registro de validación ARM64

**Estado:** no iniciado físicamente  
**Equipo actual de desarrollo:** `fer`, Ubuntu 22.04, x86_64  
**Objetivo:** Raspberry Pi 5, Ubuntu Server 24.04 ARM64 y microSD

## Evidencia requerida

| Prueba | Estado | Evidencia |
|---|---|---|
| Arranque Ubuntu 24.04 ARM64 | Pendiente | `uname -a`, `/etc/os-release` |
| Espacio y reloj | Pendiente | `preflight.sh` |
| Cuatro adaptadores USB | Pendiente | sysfs y `bluetoothctl list` |
| Imports Athena/GUI | Pendiente | instalador sin errores |
| Una Muse, 15 minutos | Pendiente | log y tasas EEG/IMU/PPG |
| Reconexión misma MAC | Pendiente | estados y contadores |
| Dos Muse, 30 minutos | Pendiente | log y SQLite |
| Cuatro Muse, dos horas | Pendiente | log, SQLite y CSV |
| Reinicio de GUI | Pendiente | colector continúa vivo |
| Corte y retorno de Wi-Fi | Pendiente | SQLite continúa y GUI recupera |

No marcar una prueba como aprobada únicamente porque compiló en x86_64.

