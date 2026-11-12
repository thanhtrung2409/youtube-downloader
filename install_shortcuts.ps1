<#
.SYNOPSIS
  Add "YouTube to MP3" shortcuts to the Start menu (and optionally the desktop).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install_shortcuts.ps1
  powershell -ExecutionPolicy Bypass -File install_shortcuts.ps1 -Desktop

  Creates two shortcuts:
    YouTube to MP3        the desktop app (no console window)
    YouTube to MP3 (web)  the local web app; its small window keeps the server running
  Remove them by deleting the shortcuts. Run it again after moving this folder.
#>
param(
    [switch]$Desktop,                                             # also put them on the desktop
    [string]$Destination = [Environment]::GetFolderPath('Programs')  # Start menu
)
$ErrorActionPreference = 'Stop'

# The real interpreter, not the WindowsApps alias: a shortcut needs a file it can point at.
$python = (& py -c "import sys; print(sys.executable)").Trim()
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "pythonw.exe not found next to $python" }

$shell = New-Object -ComObject WScript.Shell
$folders = @($Destination) + $(if ($Desktop) { [Environment]::GetFolderPath('Desktop') } else { @() })

foreach ($folder in $folders) {
    foreach ($s in @(
        @{ Name = 'YouTube to MP3';       Target = $pythonw; Script = 'yt2mp3_gui.pyw'; Window = 1 },
        @{ Name = 'YouTube to MP3 (web)'; Target = $python;  Script = 'yt2mp3_web.py';  Window = 7 }  # 7 = minimized
    )) {
        $path = Join-Path $folder "$($s.Name).lnk"
        $link = $shell.CreateShortcut($path)
        $link.TargetPath = $s.Target
        $link.Arguments = '"' + (Join-Path $PSScriptRoot $s.Script) + '"'
        $link.WorkingDirectory = $PSScriptRoot
        $link.WindowStyle = $s.Window
        $link.IconLocation = "$($s.Target),0"
        $link.Description = 'Download YouTube videos and playlists as MP3'
        $link.Save()
        Write-Host "Created $path"
    }
}
