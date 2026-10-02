$ErrorActionPreference = "Stop"
$repo = Join-Path $HOME "AURORA"
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Aurora.lnk"
$wsl = "$env:SystemRoot\System32\wsl.exe"
if (-not (Test-Path $repo)) { throw "Aurora repository was not found at $repo" }
$action = 'cd ~/AURORA && bash scripts/launch-local.sh'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $wsl
$shortcut.Arguments = '-d Ubuntu -- bash -lc "' + $action + '"'
$shortcut.WorkingDirectory = $repo
$shortcut.Description = "Launch Aurora Control Center"
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,167"
$shortcut.Save()
Write-Host "Aurora shortcut created: $shortcutPath"