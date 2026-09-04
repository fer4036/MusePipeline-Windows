#!/usr/bin/env bash
set -u

failures=0
warnings=0

pass() { printf 'PASS  %s\n' "$1"; }
warn() { printf 'WARN  %s\n' "$1"; warnings=$((warnings + 1)); }
fail() { printf 'FAIL  %s\n' "$1"; failures=$((failures + 1)); }

architecture=$(uname -m)
if [ "$architecture" = "aarch64" ]; then
  pass "Arquitectura ARM64: $architecture"
else
  fail "Se esperaba aarch64 y se obtuvo $architecture"
fi

if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  if [ "${ID:-}" = "ubuntu" ] && [ "${VERSION_ID:-}" = "24.04" ]; then
    pass "Sistema operativo Ubuntu ${VERSION_ID}"
  else
    fail "Se esperaba Ubuntu 24.04; se obtuvo ${PRETTY_NAME:-desconocido}"
  fi
else
  fail "No se pudo leer /etc/os-release"
fi

root_available_kb=$(df -Pk / | awk 'NR == 2 {print $4}')
if [ "${root_available_kb:-0}" -ge 8388608 ]; then
  pass "Espacio libre en /: $((root_available_kb / 1048576)) GiB"
else
  fail "Se requieren al menos 8 GiB libres en /"
fi

if command -v timedatectl >/dev/null 2>&1 && \
   timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -qx yes; then
  pass "Reloj sincronizado por NTP"
else
  warn "El reloj no reporta sincronización NTP"
fi

if command -v bluetoothctl >/dev/null 2>&1; then
  pass "bluetoothctl instalado"
else
  fail "Falta bluetoothctl; instala el paquete bluez"
fi

mapfile -t controllers < <(
  find /sys/class/bluetooth -mindepth 1 -maxdepth 1 -type l \
    -printf '%f\n' 2>/dev/null | grep -E '^hci[0-9]+$' | sort -V
)
if [ "${#controllers[@]}" -eq 0 ]; then
  fail "Linux no expone controladores Bluetooth"
else
  pass "Controladores detectados: ${controllers[*]}"
fi

external=0
for controller in "${controllers[@]}"; do
  device_path=$(readlink -f "/sys/class/bluetooth/$controller/device")
  usb_path=$device_path
  while [ "$usb_path" != / ] && [ ! -f "$usb_path/idVendor" ]; do
    usb_path=$(dirname "$usb_path")
  done
  if [ ! -f "$usb_path/idVendor" ]; then
    printf 'INFO  %s integrado (%s)\n' "$controller" "$device_path"
    continue
  fi
  vendor=$(tr -d '\n' < "$usb_path/idVendor")
  product=$(tr -d '\n' < "$usb_path/idProduct")
  policy=unknown
  if [ -r "$usb_path/power/control" ]; then
    policy=$(tr -d '\n' < "$usb_path/power/control")
  fi
  if [ "$controller" = hci0 ]; then
    kind="integrado/reservado"
  else
    kind="dongle"
    external=$((external + 1))
  fi
  printf 'INFO  %s %s USB %s:%s power/control=%s ruta=%s\n' \
    "$controller" "$kind" "$vendor" "$product" "$policy" \
    "$(basename "$usb_path")"
  if [ "$policy" = auto ]; then
    warn "$controller conserva autosuspensión USB"
  fi
done

if [ "$external" -ge 4 ]; then
  pass "Hay $external adaptadores Bluetooth USB para cuatro Muse"
else
  warn "Sólo hay $external adaptadores Bluetooth USB; capacidad máxima: $external"
fi

if [ -r /sys/class/thermal/thermal_zone0/temp ]; then
  temperature=$(awk '{printf "%.1f", $1 / 1000}' \
    /sys/class/thermal/thermal_zone0/temp)
  printf 'INFO  Temperatura CPU: %s °C\n' "$temperature"
fi

printf '\nResultado: %d fallas, %d advertencias.\n' "$failures" "$warnings"
if [ "$failures" -ne 0 ]; then
  exit 1
fi
