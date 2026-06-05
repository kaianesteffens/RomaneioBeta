$ErrorActionPreference = "Continue"

$Project = $env:FRETIO_PROJECT_DIR
$ResultDir = $env:FRETIO_VM_RESULT_DIR
$AppCommandFile = Join-Path $Project "scripts\opencode\windows\app-command.txt"
$AppLog = Join-Path $ResultDir "app-console.log"
$Evidence = Join-Path $ResultDir "app-evidence.txt"

if (!(Test-Path $ResultDir)) {
    New-Item -ItemType Directory -Path $ResultDir -Force | Out-Null
}

if (!(Test-Path $AppCommandFile)) {
    "ERRO: app-command.txt não encontrado em $AppCommandFile" | Out-File $Evidence -Encoding utf8
    exit 1
}

$AppCommand = (Get-Content $AppCommandFile -Raw).Trim()

"AppCommand: $AppCommand" | Out-File $Evidence -Encoding utf8
"StartedAt: $(Get-Date)" | Out-File $Evidence -Append -Encoding utf8

Set-Location $Project

$pythonPath = "$Project\app\fretio\src"
if ($env:PYTHONPATH) {
    $pythonPath = "$($env:PYTHONPATH);$pythonPath"
}

$env:PYTHONPATH = $pythonPath
$cmd = "$AppCommand > `"$AppLog`" 2>&1"

$process = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $cmd -WorkingDirectory $Project -PassThru

Start-Sleep -Seconds 12

$alive = $false
try {
    $alive = -not $process.HasExited
}
catch {
    $alive = $false
}

"ProcessId: $($process.Id)" | Out-File $Evidence -Append -Encoding utf8
"StillRunningAfter12s: $alive" | Out-File $Evidence -Append -Encoding utf8

if ($alive) {
    "RESULT: app opened and stayed running" | Out-File $Evidence -Append -Encoding utf8

    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing

        $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
        $screenshot = Join-Path $ResultDir "screenshot.png"
        $bitmap.Save($screenshot, [System.Drawing.Imaging.ImageFormat]::Png)
        $graphics.Dispose()
        $bitmap.Dispose()

        "Screenshot: $screenshot" | Out-File $Evidence -Append -Encoding utf8
    }
    catch {
        "ScreenshotError: $_" | Out-File $Evidence -Append -Encoding utf8
    }

    taskkill /PID $process.Id /T /F | Out-Null
    Get-Content $Evidence
    exit 0
}

"RESULT: app did not stay running" | Out-File $Evidence -Append -Encoding utf8

if (Test-Path $AppLog) {
    "==== app-console.log ====" | Out-File $Evidence -Append -Encoding utf8
    Get-Content $AppLog -Tail 120 | Out-File $Evidence -Append -Encoding utf8
}

Get-Content $Evidence
exit 1
