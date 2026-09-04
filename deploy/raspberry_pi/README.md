# Despliegue inicial en Raspberry Pi 5

Esta carpeta prepara y valida el agente local en una Raspberry Pi 5. Su
existencia no significa que ARM64 ya haya sido probado: cada resultado físico
debe registrarse en `VALIDACION_ARM64.md`.

## Plataforma de la primera validación

- Raspberry Pi 5.
- Ubuntu Server 24.04 LTS de 64 bits.
- microSD de al menos 16 GB; para experimentos se recomienda 32 GB A2 de alta
  resistencia.
- fuente oficial o equivalente de 5 V / 5 A y refrigeración activa.
- hub USB alimentado y adaptadores BLE individuales.
- conexión por SSH desde la computadora Linux.

ROS 2 Jazzy queda fuera de la primera prueba. El objetivo inicial es demostrar
que BlueZ, Athena, SQLite y la GUI funcionan en ARM64 mediante el backend
`standalone`. Esto aísla cualquier problema de hardware o Python antes de
introducir ROS.

## 1. Preparar la microSD

En Raspberry Pi Imager seleccionar:

1. Raspberry Pi 5.
2. Ubuntu Server 24.04 LTS (64-bit).
3. Configurar hostname `muse-edge`, usuario, contraseña, Wi-Fi y zona horaria.
4. Activar SSH con autenticación por contraseña sólo para la instalación
   inicial. Después se recomienda una llave SSH.

No se necesita monitor conectado a la Pi. Desde Linux:

```bash
ssh <usuario>@muse-edge.local
```

Si mDNS todavía no responde, obtener la IP desde el router y usar:

```bash
ssh <usuario>@<ip-de-la-raspberry>
```

## 2. Clonar la rama de trabajo

```bash
sudo apt update
sudo apt install -y git
git clone --branch feature/rasp-pi5-edge \
  https://github.com/fer4036/MusePipeline-ROS2.git ~/muse
cd ~/muse
```

La rama debe contener y haber respaldado previamente todos los cambios locales
de la computadora principal.

## 3. Ejecutar el diagnóstico previo

```bash
cd ~/muse
bash deploy/raspberry_pi/preflight.sh
```

Antes de probar cuatro usuarios, el diagnóstico debe mostrar `aarch64`, Ubuntu
24.04, reloj sincronizado, espacio suficiente y cuatro adaptadores externos.
El Bluetooth integrado normalmente será `hci0`; no se debe asumir que los
dongles conservarán siempre `hci1` a `hci4`.

## 4. Instalar el runtime Python

```bash
cd ~/muse
bash deploy/raspberry_pi/install_python_runtime.sh
```

El script crea `~/muse/.venv`, instala Athena desde el commit fijado en
`requirements_athena.txt`, instala los paquetes locales y ejecuta imports de
verificación. No instala ROS 2.

## 5. Orden de las pruebas físicas

1. Sin diademas: confirmar todos los adaptadores con `preflight.sh`.
2. Una Muse y un dongle: detectar, conectar y transmitir durante 15 minutos.
3. Una Muse: apagar, esperar `waiting_for_device`, encender y confirmar la misma
   asignación operador--MAC.
4. Dos Muse durante 30 minutos.
5. Cuatro Muse durante dos horas.
6. Reiniciar sólo la GUI y confirmar que el colector Python continúa.
7. Desconectar Wi-Fi y confirmar que SQLite continúa creciendo localmente.

La GUI se inicia manualmente durante esta fase:

```bash
cd ~/muse
source .venv/bin/activate
export MUSE_STANDALONE_PYTHON="$PWD/.venv/bin/python"
python -m muse_web.app
```

El servicio `systemd` y el arranque automático se incorporarán después de que
la prueba de una Muse sea satisfactoria. Así, un error de dependencias ARM64 no
queda oculto detrás de reinicios automáticos.

