#!/usr/bin/env bash

set -euo pipefail

readonly CONFIG_RELATIVE_PATH="config/build.config"
readonly OUTPUT_RELATIVE_DIRECTORY="out"
readonly KLIPPER_ARTIFACT="klipper.bin"
readonly FIRMWARE_ARTIFACT="firmware.bin"

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

repository_root() {
  local script_directory
  script_directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  git -C "$script_directory" rev-parse --show-toplevel 2>/dev/null \
    || fail "The script must be located inside a Git working tree."
}

write_build_configuration() {
  local config_path=$1

  cat > "$config_path" <<'EOF'
CONFIG_LOW_LEVEL_OPTIONS=y
CONFIG_MACH_STM32=y
CONFIG_MACH_STM32F103=y
CONFIG_STM32_FLASH_START_7000=y
CONFIG_STM32_CLOCK_REF_8M=y
CONFIG_STM32_USB_PA11_PA12=y
CONFIG_INITIAL_PINS="!PC13"
EOF
}

run_tools() {
  local repository_path=$1
  local command=$2

  docker compose \
    --project-directory "$repository_path" \
    -f "$repository_path/docker-compose.extra.tools.yaml" \
    run --rm tools "$command"
}

main() {
  command -v docker >/dev/null 2>&1 || fail "Docker is required."
  docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required."

  local root config_path output_directory klipper_artifact firmware_artifact
  root=$(repository_root)
  config_path="$root/$CONFIG_RELATIVE_PATH"
  output_directory="$root/$OUTPUT_RELATIVE_DIRECTORY"
  klipper_artifact="$output_directory/$KLIPPER_ARTIFACT"
  firmware_artifact="$output_directory/$FIRMWARE_ARTIFACT"

  mkdir -p "$output_directory"
  if [[ -e "$config_path" ]]; then
    printf 'Replacing %s with the SKR mini E3 v1.2 build configuration.\n' "$config_path"
  fi
  write_build_configuration "$config_path"

  printf 'Configuring Klipper for STM32F103 with a 28 KiB bootloader and USB...\n'
  run_tools "$root" "make olddefconfig"

  printf 'Building firmware...\n'
  run_tools "$root" "find out -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && make"

  [[ -s "$klipper_artifact" ]] || fail "Klipper did not produce $klipper_artifact."
  if [[ -e "$firmware_artifact" ]]; then
    printf 'Replacing existing firmware artifact: %s\n' "$firmware_artifact"
  fi
  run_tools "$root" "cp -- out/$KLIPPER_ARTIFACT out/$FIRMWARE_ARTIFACT"
  [[ -s "$firmware_artifact" ]] || fail "Klipper did not produce $firmware_artifact."

  printf '\nFirmware created: %s\n' "$firmware_artifact"
  printf '%s\n' 'Copy firmware.bin to the root of a FAT32-formatted SD card, safely eject it, then insert it into the powered-off SKR mini E3 v1.2 and turn the printer on.'
}

main "$@"
