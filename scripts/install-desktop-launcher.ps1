$ErrorActionPreference = "Stop"
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Aurora.lnk"
$wsl = "$env:SystemRoot\System32\wsl.exe"
& $wsl -d Ubuntu -- bash -lc "test -d ~/AURORA"
if ($LASTEXITCODE -ne 0) { throw "Aurora repository was not found in WSL Ubuntu at ~/AURORA" }
$action = 'cd ~/AURORA && bash scripts/launch-local.sh'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $wsl
$shortcut.Arguments = '-d Ubuntu -- bash -lc "' + $action + '"'
$shortcut.Description = "Launch Aurora Control Center"
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,167"
$shortcut.Save()
Write-Host "Aurora shortcut created: $shortcutPath"