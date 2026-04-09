# =============================================================================
# Regista — Setup do Prefect Worker no Windows (via NSSM)
# Executar como Administrador
# =============================================================================

param(
    [string]$PrefectServerUrl = "http://localhost:4200/api",
    [string]$WorkQueue       = "queue-rdp-01",
    [string]$ServiceName     = "RegistaWorker",
    [string]$InstallDir      = "C:\regista-worker",
    [string]$PythonVersion   = "3.11.9"
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}

# ---------------------------------------------------------------------------
# 1. Verificar se está rodando como Admin
# ---------------------------------------------------------------------------
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Execute este script como Administrador."
    exit 1
}

# ---------------------------------------------------------------------------
# 2. Instalar Python 3.11 (via winget, se não existir)
# ---------------------------------------------------------------------------
Write-Step "Verificando Python $PythonVersion"

$pythonExe = "C:\Python311\python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Host "Python não encontrado. Instalando via winget..."
    winget install --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
    $pythonExe = (Get-Command python).Source
} else {
    Write-Host "Python já instalado: $pythonExe"
}

# ---------------------------------------------------------------------------
# 3. Criar diretório do worker e virtualenv
# ---------------------------------------------------------------------------
Write-Step "Criando ambiente virtual em $InstallDir"

if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir | Out-Null
}

$venvPath = "$InstallDir\.venv"
if (-not (Test-Path $venvPath)) {
    & $pythonExe -m venv $venvPath
}

$pip    = "$venvPath\Scripts\pip.exe"
$python = "$venvPath\Scripts\python.exe"

# ---------------------------------------------------------------------------
# 4. Instalar dependências
# ---------------------------------------------------------------------------
Write-Step "Instalando Prefect, Playwright e dependências"

& $pip install --upgrade pip
& $pip install "prefect==2.*" playwright python-dotenv

# Instalar browsers do Playwright
& $python -m playwright install chromium
& $python -m playwright install-deps chromium

# ---------------------------------------------------------------------------
# 5. Criar arquivo .env do worker
# ---------------------------------------------------------------------------
Write-Step "Configurando variáveis de ambiente"

$envFile = "$InstallDir\.env"
if (-not (Test-Path $envFile)) {
    @"
PREFECT_API_URL=$PrefectServerUrl
"@ | Set-Content $envFile
    Write-Host "Arquivo .env criado em $envFile — ajuste o IP do servidor se necessário."
} else {
    Write-Host ".env já existe, mantendo configuração atual."
}

# ---------------------------------------------------------------------------
# 6. Criar script de inicialização do worker
# ---------------------------------------------------------------------------
Write-Step "Criando script de start do worker"

$startScript = "$InstallDir\start_worker.ps1"
@"
Set-Location "$InstallDir"
`$env:PREFECT_API_URL = "$PrefectServerUrl"
& "$venvPath\Scripts\prefect.exe" worker start --pool default-agent-pool --work-queue $WorkQueue
"@ | Set-Content $startScript

# ---------------------------------------------------------------------------
# 7. Instalar NSSM e registrar serviço Windows
# ---------------------------------------------------------------------------
Write-Step "Verificando NSSM"

$nssmExe = (Get-Command nssm -ErrorAction SilentlyContinue)?.Source
if (-not $nssmExe) {
    Write-Host "NSSM não encontrado. Tentando instalar via winget..."
    winget install --id NSSM.NSSM --silent --accept-package-agreements --accept-source-agreements
    $nssmExe = (Get-Command nssm).Source
}

Write-Step "Registrando serviço Windows '$ServiceName'"

$powershellExe = (Get-Command powershell).Source

# Remover serviço anterior se existir
$existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existingService) {
    Write-Host "Removendo serviço existente..."
    & $nssmExe stop $ServiceName
    & $nssmExe remove $ServiceName confirm
}

& $nssmExe install $ServiceName $powershellExe "-ExecutionPolicy Bypass -File `"$startScript`""
& $nssmExe set $ServiceName AppDirectory $InstallDir
& $nssmExe set $ServiceName DisplayName "Regista Prefect Worker ($WorkQueue)"
& $nssmExe set $ServiceName Description "Worker do Regista para a fila $WorkQueue"
& $nssmExe set $ServiceName Start SERVICE_AUTO_START
& $nssmExe set $ServiceName AppStdout "$InstallDir\logs\worker_stdout.log"
& $nssmExe set $ServiceName AppStderr "$InstallDir\logs\worker_stderr.log"
& $nssmExe set $ServiceName AppRotateFiles 1
& $nssmExe set $ServiceName AppRotateBytes 10485760

New-Item -ItemType Directory -Path "$InstallDir\logs" -Force | Out-Null

# ---------------------------------------------------------------------------
# 8. Iniciar serviço
# ---------------------------------------------------------------------------
Write-Step "Iniciando serviço $ServiceName"
& $nssmExe start $ServiceName

Start-Sleep -Seconds 3
$svc = Get-Service -Name $ServiceName
Write-Host "Status do servico: $($svc.Status)" -ForegroundColor $(if ($svc.Status -eq 'Running') {'Green'} else {'Red'})

Write-Host "`n✔ Setup concluido!" -ForegroundColor Green
Write-Host "  Worker:      $ServiceName"
Write-Host "  Fila:        $WorkQueue"
Write-Host "  Prefect API: $PrefectServerUrl"
Write-Host "  Logs:        $InstallDir\logs\"
Write-Host "`nPara verificar: Get-Service $ServiceName"
Write-Host "Para parar:     Stop-Service $ServiceName"
