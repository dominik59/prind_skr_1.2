#!/usr/bin/env bash

set -euo pipefail

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

script_directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(git -C "$script_directory" rev-parse --show-toplevel 2>/dev/null) \
  || fail "The script must be located inside a Git working tree."

command -v docker >/dev/null 2>&1 || fail "Docker is required."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required."

cd "$repository_root"
docker compose --profile mainsail up -d
printf 'Mainsail stack is running.\n'

