#!/usr/bin/env bash
# Source this file, then call aurora_load_local_env. Never execute config values.
aurora_load_local_env() {
  local config_file="${AURORA_LOCAL_ENV_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/aurora/aurora.env}"
  local config_dir name value

  if [[ -L "$config_file" ]]; then
    echo "Aurora local configuration must not be a symbolic link." >&2
    return 1
  fi
  [[ -e "$config_file" ]] || return 0
  if [[ ! -f "$config_file" ]]; then
    echo "Aurora local configuration is not a regular file." >&2
    return 1
  fi

  config_dir="$(dirname -- "$config_file")"
  if [[ ! -O "$config_dir" || ! -O "$config_file" ]]; then
    echo "Aurora local configuration must belong to the current user." >&2
    return 1
  fi
  chmod 700 "$config_dir" || {
    echo "Could not restrict Aurora configuration directory permissions." >&2
    return 1
  }
  chmod 600 "$config_file" || {
    echo "Could not restrict Aurora credential-file permissions." >&2
    return 1
  }

  while IFS='=' read -r name value || [[ -n "${name:-}" ]]; do
    case "$name" in
      ""|\#*) continue ;;
      OPENROUTER_API_KEY)
        if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
          echo "Invalid line break in Aurora API credential." >&2
          return 1
        fi
        export OPENROUTER_API_KEY="$value"
        ;;
      AURORA_DIRECTOR_PROVIDER)
        case "$value" in
          nvidia_api|openrouter|router) export AURORA_DIRECTOR_PROVIDER="$value" ;;
          *)
            echo "Unsupported AURORA_DIRECTOR_PROVIDER in local configuration." >&2
            return 1
            ;;
        esac
        ;;
      OPENROUTER_MODEL_ID)
        if [[ "$value" != "openrouter/free" ]]; then
          echo "Local configuration permits only OPENROUTER_MODEL_ID=openrouter/free." >&2
          return 1
        fi
        export OPENROUTER_MODEL_ID="$value"
        ;;
      *)
        echo "Unsupported setting name in Aurora local configuration: $name" >&2
        return 1
        ;;
    esac
  done < "$config_file"

  if [[ "${AURORA_DIRECTOR_PROVIDER:-router}" == "openrouter" && -z "${OPENROUTER_API_KEY:-}" ]]; then
    echo "OpenRouter is selected but no local API key is configured." >&2
    return 1
  fi
}
