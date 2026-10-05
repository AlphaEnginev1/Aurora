$ErrorActionPreference = "Stop"
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Aurora.lnk"
$wsl = "$env:SystemRoot\System32\wsl.exe"
& $wsl -d Ubuntu -- bash -lc "test -d ~/AURORA"
if ($LASTEXITCODE -ne 0) { throw "Aurora repository was not found in WSL Ubuntu at ~/AURORA" }
# Keep the Windows shortcut as a thin entry point. The authoritative startup sequence remains in WSL.
$action = 'cd ~/AURORA && exec bash scripts/launch-local.sh'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $wsl
$shortcut.Arguments = '-d Ubuntu --cd ~ -- bash -lc "' + $action + '"'
$shortcut.WorkingDirectory = $desktop
$shortcut.Description = "Launch Aurora Control Center"
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,167"
$shortcut.Save()
Write-Host "Aurora shortcut created: $shortcutPath"