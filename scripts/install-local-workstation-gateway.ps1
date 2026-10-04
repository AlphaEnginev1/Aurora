$ErrorActionPreference = "Stop"

param(
  [string]$Python = "python",
  [string]$InstallRoot = "$env:LOCALAPPDATA\AuroraLocalGateway"
)

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
& $Python -m pip install --upgrade pyautogui pillow
Write-Host "Aurora local gateway dependencies installed."
Write-Host "Set AURORA_LOCAL_GATEWAY_TOKEN and AURORA_LOCAL_ROOTS before launching scripts/local-workstation-gateway.py."
