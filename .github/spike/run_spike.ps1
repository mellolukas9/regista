$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Force C:\spike\out, C:\spike\src | Out-Null
Copy-Item -Recurse -Force agent\src\regista_agent C:\spike\src\regista_agent
$env:UV_PYTHON_INSTALL_DIR = "C:\spike\python"
uv python install 3.13
uv venv C:\spike\venv --python 3.13
uv pip install --python C:\spike\venv\Scripts\python.exe playwright psutil
$env:PLAYWRIGHT_BROWSERS_PATH = "C:\spike\browsers"
& C:\spike\venv\Scripts\playwright.exe install chromium
icacls C:\spike /grant "BUILTIN\Users:(OI)(CI)RX" /T | Out-Null
$py = "C:\spike\venv\Scripts\python.exe"
$svc = @{ agent = "SpikeAgent"; robot = "SpikeRobot" }
foreach ($r in "agent", "robot") {
  $name = $svc[$r]
  sc.exe create $name binPath= "`"$py`" `"$PWD\.github\spike\spike_svc.py`" $r" obj= "NT SERVICE\$name" start= demand | Out-Host
  sc.exe sidtype $name unrestricted | Out-Host
}
foreach ($n in "SpikeAgent", "SpikeRobot") { icacls C:\spike\out /grant "NT SERVICE\${n}:(OI)(CI)M" | Out-Null }
icacls "$PWD\.github\spike" /grant "BUILTIN\Users:(OI)(CI)RX" | Out-Null
sc.exe start SpikeAgent | Out-Host
Start-Sleep 3
@'
import sys
sys.path.insert(0, r"C:\spike\src")
from regista_agent import winpipe
try:
    winpipe.connect("regista-spike-host", timeout=3)
    print("INTRUDER CONNECTED (bad)")
except Exception as e:
    print("INTRUDER refused:", e)
'@ | Set-Content C:\spike\intruder.py
& $py C:\spike\intruder.py
sc.exe start SpikeRobot | Out-Host
$deadline = (Get-Date).AddSeconds(240)
while ((Get-Date) -lt $deadline -and -not ((Test-Path C:\spike\out\agent-result.json) -and (Test-Path C:\spike\out\robot-result.json))) { Start-Sleep 3 }
Get-ChildItem C:\spike\out | ForEach-Object { "=== $($_.Name)"; Get-Content $_.FullName }
sc.exe query SpikeAgent | Out-Host
sc.exe query SpikeRobot | Out-Host
foreach ($n in "SpikeRobot", "SpikeAgent") { sc.exe stop $n | Out-Null; sc.exe delete $n | Out-Null }
