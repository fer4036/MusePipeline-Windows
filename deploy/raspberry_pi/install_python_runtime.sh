#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/../.." && pwd)
venv_path="$repo_root/.venv"

if [ "$(uname -m)" != aarch64 ]; then
  printf 'Este instalador está reservado para la Raspberry ARM64.\n' >&2
  exit 1
fi

sudo apt update
sudo apt install -y \
  avahi-daemon \
  avahi-utils \
  bluez \
  build-essential \
  git \
  libdbus-1-dev \
  libglib2.0-dev \
  python3-dev \
  python3-pip \
  python3-venv \
  sqlite3

python3 -m venv "$venv_path"
"$venv_path/bin/python" -m pip install --upgrade pip setuptools wheel
"$venv_path/bin/python" -m pip install -r "$repo_root/requirements_athena.txt"
"$venv_path/bin/python" -m pip install -r "$repo_root/muse_web/requirements.txt"
"$venv_path/bin/python" -m pip install -e "$repo_root/muse_hrc"
"$venv_path/bin/python" -m pip install -e "$repo_root/muse_web"

"$venv_path/bin/python" -c \
  'from muselsl.athena import Athena; from muse_hrc.athena_adapter import AdapterAthena; from muse_web.app import app; print("Runtime ARM64 importado correctamente")'

mkdir -p "$HOME/MuseResearch/sessions"
chmod 700 "$HOME/MuseResearch" "$HOME/MuseResearch/sessions"

printf '\nRuntime instalado en %s\n' "$venv_path"
printf 'Siguiente paso: ejecutar deploy/raspberry_pi/preflight.sh y probar una Muse.\n'

