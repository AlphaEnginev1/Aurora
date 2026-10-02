#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "
  \$shell = New-Object -ComObject WScript.Shell
  \$shortcut = \$shell.CreateShortcut((Join-Path [Environment]::GetFolderPath('Desktop') 'Aurora.lnk'))
  \$shortcut.TargetPath = Join-Path \$env:SystemRoot 'System32\\wsl.exe'
  \$shortcut.Arguments = '-d Ubuntu -- bash -lc "cd ~/AURORA && bash scripts/launch-local.sh"'
  \$shortcut.Description = 'Launch Aurora Control Center'
  \$shortcut.IconLocation = Join-Path \$env:SystemRoot 'System32\\shell32.dll,167'
  \$shortcut.Save()
"
echo "Aurora desktop shortcut installed."