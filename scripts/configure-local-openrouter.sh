#!/usr/bin/env bash
set -euo pipefail

config_file="${AURORA_LOCAL_ENV_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/aurora/aurora.env}"
config_dir="$(dirname -- "$config_file")"
mkdir -p -- "$config_dir"
if [[ ! -O "$config_dir" ]]; then
  echo "The Aurora configuration directory must belong to the current user." >&2
  exit 1
fi
chmod 700 "$config_dir"

key="${OPENROUTER_API_KEY:-}"
if [[ -z "$key" ]]; then
  read -r -s -p "Pega la clave de OpenRouter (no se mostrará): " key
  printf '\n'
fi
if [[ -z "$key" || "$key" == *$'\n'* || "$key" == *$'\r'* ]]; then
  unset key
  echo "Clave vacía o con saltos de línea; no se guardó nada." >&2
  exit 1
fi

tmp_file=""
cleanup() {
  if [[ -n "$tmp_file" ]]; then
    rm -f -- "$tmp_file"
  fi
  unset key
}
trap cleanup EXIT

tmp_file="$(mktemp "$config_dir/.aurora.env.XXXXXX")"
printf 'OPENROUTER_API_KEY=%s\nAURORA_DIRECTOR_PROVIDER=openrouter\nOPENROUTER_MODEL_ID=openrouter/free\n' "$key" > "$tmp_file"
chmod 600 "$tmp_file"
mv -f -- "$tmp_file" "$config_file"
tmp_file=""
unset key
trap - EXIT

echo "Configuración local guardada con permisos restringidos."
echo "La clave no se ha mostrado ni se ha escrito en el repositorio."
