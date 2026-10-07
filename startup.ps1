<#
.SYNOPSIS
    startup.ps1: bootstrap and run Neural Reader on Windows (TTS backend +
    Postgres + frontend). The Windows counterpart of startup.sh, same commands.

.DESCRIPTION
    .\startup.ps1 init [podman|docker]   Check prerequisites, set up venv + deps,
                                         download models, install + build frontend.
    .\startup.ps1 up                     Start Postgres + SearXNG + the TTS backend
                                         (run.py) with auth DISABLED: quick
                                         single-user dev (loopback bind only).
    .\startup.ps1 up-with-dev-auth       Local OIDC rig: also starts Keycloak,
                                         creates .env on first run (local realm
                                         values + a generated SESSION_SECRET),
                                         waits for the realm import, then runs
                                         the backend with auth ENABLED.
                                         Walkthrough: deploy/README.md.
    .\startup.ps1 down                   Stop the TTS backend, then stop all the
                                         containers (Postgres, SearXNG, Keycloak).
    .\startup.ps1 help                   Show usage.

    Runs on Windows PowerShell 5.1 (built into Windows 10/11) and PowerShell 7+.
    If scripts are blocked by the execution policy, use startup.cmd, which runs
    this file with -ExecutionPolicy Bypass for this one invocation.

    The container engine is resolved in this order: explicit arg to `init` ->
    $env:CONTAINER_ENGINE -> the choice saved by a previous `init` ->
    auto-detect (docker preferred, then podman).
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)] [string] $Command = "up",
    [Parameter(Position = 1)] [string] $Engine = ""
)

$ErrorActionPreference = "Stop"
# Invoke-WebRequest's progress bar makes big downloads ~10x slower on 5.1.
$ProgressPreference = "SilentlyContinue"

# Always operate from the project root (this script's own directory).
Set-Location -LiteralPath $PSScriptRoot

$ComposeFile = "docker-compose.yml"
$VenvDir = ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$PostgresService = "postgres"
$PostgresUser = "natural_reader"
$KeycloakService = "keycloak"
$KeycloakDiscoveryUrl = "http://localhost:18080/realms/natural-reader/.well-known/openid-configuration"
$EngineStateFile = ".local\container-engine"   # .local/ is gitignored
$RunnerPidFile = ".local\runner.pid"            # PID of the run.py backend
$EnvFile = ".env"                               # gitignored; loaded for run.py

$ModelBaseUrl = "https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0"
$ModelFiles = @("kokoro-v1.0.onnx", "voices-v1.0.bin")

# Minimum tool versions, kept in sync with the README "Software Requirements"
# and startup.sh. Python: the chat server uses inspect.getasyncgenstate (3.12+),
# and onnxruntime-openvino (pinned in requirements.txt for Kokoro) publishes
# wheels only for Python <3.14.
$PythonMin = [version]"3.12.0"
$PythonMaxExcl = [version]"3.14.0"
# Tried in order; the first in range wins. The default Python first, then an
# older one through the py launcher, so a too-new default falls through
# (startup.sh does the same with python3, python3.13, python3.12).
$PythonCandidates = @(
    @("py", "-3"), @("py", "-3.13"), @("py", "-3.12"),
    @("python"), @("python3")
)

function Write-Step([string] $Message) { Write-Host "==> " -ForegroundColor Blue -NoNewline; Write-Host $Message }
function Write-Warn([string] $Message) { Write-Host "warning: " -ForegroundColor Yellow -NoNewline; Write-Host $Message }
function Stop-WithError([string] $Message) {
    Write-Host "error: " -ForegroundColor Red -NoNewline; Write-Host $Message
    exit 1
}

function Test-Command([string] $Name) { [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

# A small text file's trimmed content, "" if missing or empty (Get-Content
# -Raw returns $null for an empty file).
function Read-SmallFile([string] $Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return "" }
    return "$(Get-Content -LiteralPath $Path -Raw)".Trim()
}

# The process recorded in a pidfile, or $null.
function Get-RecordedProcess([string] $PidFile) {
    $id = (Read-SmallFile $PidFile) -as [int]
    if (-not $id) { return $null }
    return Get-Process -Id $id -ErrorAction SilentlyContinue
}

# Run a native command and stop on a non-zero exit code. PowerShell doesn't
# do this by itself: a failed `pip install` would otherwise roll on.
function Invoke-Native {
    param([Parameter(Mandatory)] [string] $FilePath, [string[]] $Arguments = @())
    # Native tools write warnings to stderr (pip does); only the exit code
    # decides. Under "Stop", Windows PowerShell 5.1 can turn stderr into errors.
    $ErrorActionPreference = "Continue"
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) { Stop-WithError "'$FilePath $($Arguments -join ' ')' failed (exit code $LASTEXITCODE)." }
}

# "v20.19.0", "3.12.4", "22.12.0-rc1" -> [version]; $null if unreadable.
function ConvertTo-Version([string] $Text) {
    if ($Text -match '(\d+)\.(\d+)(?:\.(\d+))?') {
        $patch = 0
        if ($Matches[3]) { $patch = [int]$Matches[3] }
        return [version]::new([int]$Matches[1], [int]$Matches[2], $patch)
    }
    return $null
}

# The X.Y.Z version a Python command runs, or "" if it doesn't run (a missing
# launcher version, or the Microsoft Store stub that `python.exe` often is on
# a fresh Windows: it opens the Store instead of running anything).
function Get-PythonVersionText([string[]] $Cmd) {
    $ErrorActionPreference = "Continue"   # a probe's stderr must not throw (5.1)
    if (-not (Test-Command $Cmd[0])) { return "" }
    $exe, $pre = $Cmd[0], @($Cmd | Select-Object -Skip 1)
    $text = & $exe @pre -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>$null
    if ($LASTEXITCODE -ne 0) { return "" }
    return "$text".Trim()
}

# The first candidate Python in [$PythonMin, $PythonMaxExcl), as the command
# array that runs it; stops with an explanation if there is none.
function Assert-PythonVersion {
    foreach ($cmd in $PythonCandidates) {
        $text = Get-PythonVersionText $cmd
        $v = ConvertTo-Version $text
        if ($v -and $v -ge $PythonMin -and $v -lt $PythonMaxExcl) {
            Write-Step "Python $text OK via $($cmd -join ' ') (need >=$PythonMin, <$PythonMaxExcl)"
            return , $cmd
        }
    }
    Stop-WithError ("No Python in [$PythonMin, $PythonMaxExcl) found: install Python 3.12 or 3.13 from " +
        "https://www.python.org/downloads/ and tick 'Add to PATH' (onnxruntime-openvino has no 3.14 wheels).")
}

# Node ^20.19.0 || >=22.12.0: Vite 7 (Rolldown) + @vitejs/plugin-react.
function Test-NodeVersionOk([version] $V) {
    if (-not $V) { return $false }
    return (($V -ge [version]"20.19.0" -and $V -lt [version]"21.0.0") -or $V -ge [version]"22.12.0")
}

function Assert-NodeVersion {
    if (-not (Test-Command "node")) { Stop-WithError "node is not installed (https://nodejs.org/)." }
    if (-not (Test-Command "npm")) { Stop-WithError "npm is not installed." }
    $text = (& node -v).Trim()
    if (Test-NodeVersionOk (ConvertTo-Version $text)) {
        Write-Step "Node.js $text OK (need ^20.19.0 or >=22.12.0)"
    } else {
        Stop-WithError "Node.js $text is unsupported: need ^20.19.0 or >=22.12.0 (Vite 7 / Rolldown)."
    }
}

function Show-Usage {
    @"
Usage: .\startup.ps1 <command> [options]     (or: startup.cmd <command> [options])

Commands:
  init [podman|docker]   Check prerequisites, create the Python venv, install
                         backend deps, download Kokoro models, install + build
                         the frontend. The engine choice is remembered.
  up                     Start Postgres + SearXNG containers and the TTS backend
                         (run.py) with auth DISABLED: quick single-user dev.
                         Refuses to start if the backend port is already in use.
                         Foreground; Ctrl-C stops the backend and the containers.
  up-with-dev-auth       Full local OIDC rig: starts Postgres + SearXNG +
                         Keycloak, creates .env on first run (local realm values
                         + generated SESSION_SECRET), waits for the realm
                         import, then runs the backend with auth ENABLED.
                         Log in at http://localhost:5173 as admin-user/password.
                         Walkthrough: deploy/README.md.
  down                   Stop the TTS backend (if running) and all the
                         containers (Postgres, SearXNG, Keycloak).
  help                   Show this message.

Environment:
  CONTAINER_ENGINE       Override the container engine (podman|docker).
  WORKERS, HOST, PORT    Passed through to run.py (see run.py for details).
  AUTH_ENABLED, OIDC_*   Read from .env when it exists (see .env.example);
                         'up' overrides AUTH_ENABLED to false for the
                         single-user dev bypass (loopback bind only).
"@
}

# Resolve which container engine to use. Order of precedence:
#   1. explicit argument  2. $env:CONTAINER_ENGINE  3. saved state  4. auto-detect.
function Resolve-Engine([string] $Requested = "") {
    $choice = $Requested
    if (-not $choice) { $choice = $env:CONTAINER_ENGINE }
    if (-not $choice) { $choice = Read-SmallFile $EngineStateFile }
    if (-not $choice) {
        if (Test-Command "docker") { $choice = "docker" }
        elseif (Test-Command "podman") { $choice = "podman" }
        else { Stop-WithError "Neither docker nor podman is installed (Docker Desktop or Podman Desktop)." }
    }
    if ($choice -ne "podman" -and $choice -ne "docker") {
        Stop-WithError "Invalid container engine '$choice'. Use 'podman' or 'docker'."
    }
    if (-not (Test-Command $choice)) { Stop-WithError "$choice is not installed." }
    return $choice
}

# The compose command for an engine, as an array. For podman, prefer
# podman-compose (`podman compose` only delegates to the first provider it
# finds); for docker, prefer the v2 plugin. Both fall back to "<engine>-compose".
function Get-ComposeCommand([string] $Engine) {
    $ErrorActionPreference = "Continue"
    if ($Engine -eq "podman" -and (Test-Command "podman-compose")) { return @("podman-compose") }
    & $Engine compose version *> $null
    if ($LASTEXITCODE -eq 0) { return @($Engine, "compose") }
    if (Test-Command "$Engine-compose") { return @("$Engine-compose") }
    Stop-WithError "No compose support for $Engine (need '$Engine compose' or '$Engine-compose')."
}

# Run a compose subcommand; returns its exit code (callers decide whether a
# failure is fatal, as `compose exec pg_isready` is expected to fail at first).
function Invoke-Compose {
    param([string] $Engine, [string[]] $Arguments, [switch] $Quiet)
    $ErrorActionPreference = "Continue"   # pg_isready fails on purpose until ready
    $cmd = Get-ComposeCommand $Engine
    $exe, $pre = $cmd[0], @($cmd | Select-Object -Skip 1)
    $all = @($pre) + @("-f", $ComposeFile) + @($Arguments)
    # Out-Host: the tool's output is shown, never returned with the exit code.
    if ($Quiet) { & $exe @all *> $null } else { & $exe @all | Out-Host }
    return $LASTEXITCODE
}

# Download to a ".partial" file and move it into place only on success, so an
# interrupted transfer never leaves a truncated file that later looks complete.
function Invoke-Download([string] $Url, [string] $Destination) {
    $tmp = "$Destination.partial"
    Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
    try {
        Invoke-WebRequest -Uri $Url -OutFile $tmp -UseBasicParsing
    } catch {
        Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
        Stop-WithError "Download failed: $Url ($($_.Exception.Message))"
    }
    if (-not (Test-Path -LiteralPath $tmp) -or (Get-Item -LiteralPath $tmp).Length -eq 0) {
        Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
        Stop-WithError "Download produced an empty file: $Url"
    }
    Move-Item -LiteralPath $tmp -Destination $Destination -Force
}

# Poll until Postgres accepts connections. If it never comes up, warn but go
# on: TTS works without Postgres (only chat/RAG routes degrade to 503).
function Wait-Postgres([string] $Engine) {
    $retries = 30
    Write-Step "Waiting for Postgres to accept connections..."
    for ($i = 1; $i -le $retries; $i++) {
        $code = Invoke-Compose $Engine @("exec", "-T", $PostgresService, "pg_isready", "-U", $PostgresUser) -Quiet
        if ($code -eq 0) { Write-Step "Postgres is ready."; return }
        Start-Sleep -Seconds 1
    }
    Write-Warn "Postgres not ready after ${retries}s; continuing (TTS still works, RAG may 503)."
}

# Keycloak needs the `keycloak` schema, which the init script only creates on a
# FRESH data volume: create it before Keycloak starts (a no-op when present).
function Initialize-KeycloakSchema([string] $Engine) {
    Write-Step "Ensuring 'keycloak' schema exists in Postgres..."
    $code = Invoke-Compose $Engine @("exec", "-T", $PostgresService, "psql", "-U", $PostgresUser,
        "-d", $PostgresUser, "-c", "CREATE SCHEMA IF NOT EXISTS keycloak") -Quiet
    if ($code -eq 0) { Write-Step "Keycloak schema present." }
    else { Write-Warn "Could not ensure the keycloak schema (Postgres not ready?). If the realm import fails, see deploy/postgres/init/00-create-keycloak-schema.sql." }
}

# End a process and everything it started. With WORKERS > 1, run.py's
# uvicorn workers are child processes: ending only the parent would leave them
# holding the port. taskkill /T ends the tree (Windows); elsewhere (PowerShell
# 7 on Linux or macOS, for testing) fall back to the one process.
function Stop-ProcessTree([int] $Id) {
    $ErrorActionPreference = "Continue"
    if (Test-Command "taskkill.exe") {
        & taskkill.exe /PID $Id /T /F *> $null
    } else {
        Stop-Process -Id $Id -Force -ErrorAction SilentlyContinue
    }
}

# Stop the run.py backend recorded in the pidfile, if it is still alive, then
# remove the pidfile. Windows has no SIGTERM for console programs: wait a few
# seconds for a Ctrl-C to land (it reaches every process on this console),
# then end it. Idempotent; used by `down` and by `up`'s shutdown.
function Stop-Runner {
    if (-not (Test-Path -LiteralPath $RunnerPidFile)) { return }
    $proc = Get-RecordedProcess $RunnerPidFile
    if ($proc) {
        Write-Step "Stopping Neural Voice Server (pid $($proc.Id))"
        if (-not $proc.WaitForExit(5000)) { Stop-ProcessTree $proc.Id }
    }
    Remove-Item -LiteralPath $RunnerPidFile -ErrorAction SilentlyContinue
}

# Write a text file as UTF-8 without a BOM and with LF line endings. Windows
# PowerShell 5.1's Set-Content -Encoding UTF8 adds a BOM, which docker compose
# reads into the first key's name.
function Write-TextFile([string] $Path, [string] $Text) {
    $full = Join-Path (Get-Location) $Path
    [System.IO.File]::WriteAllText($full, ($Text -replace "`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))
}

# n random bytes as lowercase hex (openssl rand -hex n).
function New-HexSecret([int] $Bytes = 32) {
    $buf = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buf) } finally { $rng.Dispose() }
    return (($buf | ForEach-Object { $_.ToString("x2") }) -join "")
}

# A URL-safe random secret, well over the backend's 32-character
# SESSION_SECRET minimum (server/auth/config.py:MIN_SESSION_SECRET_LEN).
function New-SessionSecret {
    $buf = New-Object byte[] 48
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buf) } finally { $rng.Dispose() }
    return ([Convert]::ToBase64String($buf).TrimEnd("=").Replace("+", "-").Replace("/", "_"))
}

# Create searxng/settings.yml from the committed template on first run, so
# SearXNG comes up with JSON output enabled (the web_search tool needs it).
# Idempotent: an existing file is never touched.
function Initialize-SearxngConfig {
    $example = "searxng\settings.yml.example"
    $target = "searxng\settings.yml"
    if (Test-Path -LiteralPath $target) { return }
    if (-not (Test-Path -LiteralPath $example)) {
        Write-Warn "$example missing: SearXNG may 403 (no JSON) until you create $target."
        return
    }
    Write-Step "Creating $target from template (first run)"
    $text = [System.IO.File]::ReadAllText((Join-Path (Get-Location) $example))
    Write-TextFile $target ($text.Replace("CHANGE_ME_openssl_rand_hex_32", (New-HexSecret 32)))
}

# Create .env on first `up-with-dev-auth` with the local Keycloak rig values
# (deploy/README.md). Idempotent: an existing .env is never touched.
function Initialize-EnvFile {
    if (Test-Path -LiteralPath $EnvFile) { return }
    Write-Step "Creating $EnvFile with the local-dev OIDC rig values (first run)"
    $lines = @(
        "# Created by startup.ps1 up-with-dev-auth: local-dev OIDC rig.",
        "# Full annotated reference: .env.example. This file is gitignored.",
        "# One KEY=value per line, no inline comments.",
        "OIDC_ISSUER=http://localhost:18080/realms/natural-reader",
        "OIDC_CLIENT_ID=natural-reader",
        "OIDC_CLIENT_SECRET=natural-reader-dev-secret",
        "OIDC_REDIRECT_URL=http://localhost:5173/v1/auth/callback",
        "SESSION_SECRET=$(New-SessionSecret)",
        "COOKIE_SECURE=false",
        "BOOTSTRAP_ADMIN_EMAIL=admin@example.com"
    )
    Write-TextFile $EnvFile (($lines -join "`n") + "`n")
}

# The KEY=value pairs of a .env file, in order: blank lines and # comments
# skipped, an optional "export " dropped, matching single or double quotes
# around a value removed (what bash's `source` does for this file's shape).
function Read-EnvFile([string] $Path) {
    $pairs = New-Object System.Collections.Generic.List[object]
    foreach ($raw in [System.IO.File]::ReadAllLines((Resolve-Path -LiteralPath $Path))) {
        $line = $raw.Trim()
        if (-not $line -or $line.StartsWith("#")) { continue }
        if ($line.StartsWith("export ")) { $line = $line.Substring(7).TrimStart() }
        $eq = $line.IndexOf("=")
        if ($eq -lt 1) { continue }
        $key = $line.Substring(0, $eq).Trim()
        $value = $line.Substring($eq + 1).Trim()
        if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[-1] -eq '"') -or ($value[0] -eq "'" -and $value[-1] -eq "'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $pairs.Add([pscustomobject]@{ Key = $key; Value = $value })
    }
    return , $pairs
}

# Put every KEY=value in .env into this process's environment, which run.py
# inherits (the backend has no dotenv loader of its own). docker compose also
# reads .env itself for its ${VAR:-default} interpolations.
function Import-EnvFile {
    if (-not (Test-Path -LiteralPath $EnvFile)) { return }
    Write-Step "Loading $EnvFile"
    foreach ($pair in (Read-EnvFile $EnvFile)) {
        [System.Environment]::SetEnvironmentVariable($pair.Key, $pair.Value, "Process")
    }
}

# True if something accepts TCP connections on host:port.
function Test-PortInUse([string] $HostName, [int] $Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne(500)) { return $false }
        $client.EndConnect($connect)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

# Refuse to start a second backend on an occupied port: run.py would die at
# once with "address already in use", and the OLD backend (possibly with stale
# settings) would keep serving while `up` looked like it worked.
function Assert-BackendPortFree {
    $hostName = $env:HOST
    if (-not $hostName) { $hostName = "127.0.0.1" }
    $port = 8000
    if ($env:PORT) { $port = [int]$env:PORT }
    $running = Get-RecordedProcess $RunnerPidFile
    if ($running) {
        Stop-WithError "Backend already running (pid $($running.Id)): run '.\startup.ps1 down' first."
    }
    # A bind on all interfaces is reachable via loopback; probe that.
    if ($hostName -eq "0.0.0.0" -or $hostName -eq "::") { $hostName = "127.0.0.1" }
    if (Test-PortInUse $hostName $port) {
        Stop-WithError "Port $port is already in use (HOST=$hostName): another backend is likely still running. Stop it ('.\startup.ps1 down', or end the process) and retry."
    }
}

# Poll until Keycloak serves the realm's OIDC discovery document (the realm
# import takes ~20-40 s on first start). Warn and go on after a timeout.
function Wait-Keycloak {
    $retries = 120
    Write-Step "Waiting for Keycloak realm import..."
    for ($i = 1; $i -le $retries; $i++) {
        try {
            Invoke-WebRequest -Uri $KeycloakDiscoveryUrl -UseBasicParsing -TimeoutSec 5 | Out-Null
            Write-Step "Keycloak realm is ready."
            return
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    Write-Warn "Keycloak discovery not answering after $($retries * 2)s; login will 503 until it is up."
}

function Invoke-Init([string] $Requested) {
    $engineName = Resolve-Engine $Requested
    Write-Step "Using container engine: $engineName"

    # --- Prerequisite checks (existence + correct versions) ------------------
    $py = Assert-PythonVersion
    Assert-NodeVersion
    if (-not (Test-Path -LiteralPath "package.json")) { Stop-WithError "package.json not found: are you in the project root?" }

    # --- Python backend ------------------------------------------------------
    $exe, $pre = $py[0], @($py | Select-Object -Skip 1)
    $want = Get-PythonVersionText $py
    $have = ""
    if (Test-Path -LiteralPath $VenvPython) { $have = Get-PythonVersionText @((Resolve-Path -LiteralPath $VenvPython).Path) }
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Write-Step "Creating virtual environment with Python $want"
        Invoke-Native $exe (@($pre) + @("-m", "venv", $VenvDir))
    } elseif ($have -ne $want) {
        # Built by another Python (e.g. a 3.14 one): its packages won't fit.
        Write-Step "Recreating virtual environment: was Python $have, need $want"
        Remove-Item -LiteralPath $VenvDir -Recurse -Force
        Invoke-Native $exe (@($pre) + @("-m", "venv", $VenvDir))
    } else {
        Write-Step "Virtual environment already exists at $VenvDir\ (Python $have)"
    }
    Write-Step "Installing Python dependencies from requirements.txt"
    Invoke-Native $VenvPython @("-m", "pip", "install", "--upgrade", "pip")
    Invoke-Native $VenvPython @("-m", "pip", "install", "-r", "requirements.txt")

    # --- Kokoro model files --------------------------------------------------
    # Non-empty, not merely present: a zero-byte leftover from an aborted run
    # is fetched again rather than trusted.
    foreach ($model in $ModelFiles) {
        if ((Test-Path -LiteralPath $model) -and (Get-Item -LiteralPath $model).Length -gt 0) {
            Write-Step "Model already present, skipping: $model"
        } else {
            if (Test-Path -LiteralPath $model) { Write-Warn "Existing $model is empty/incomplete: re-downloading" }
            Write-Step "Downloading $model"
            Invoke-Download "$ModelBaseUrl/$model" $model
        }
    }

    # --- Frontend ------------------------------------------------------------
    Write-Step "Installing frontend dependencies (npm install)"
    Invoke-Native "npm" @("install")
    Write-Step "Building frontend (npm run build)"
    Invoke-Native "npm" @("run", "build")

    # Remember the engine so `up`/`down` don't need it re-specified.
    New-Item -ItemType Directory -Force -Path (Split-Path $EngineStateFile) | Out-Null
    Write-TextFile $EngineStateFile $engineName

    Write-Step "Init complete. Run '.\startup.ps1 up' to start."
}

# Shared `up`. $Mode = "dev-auth" (Keycloak rig, auth on) or "" (quick
# single-user dev, auth bypassed). Foreground: Ctrl-C (or the backend exiting)
# stops the backend and brings the containers down.
function Invoke-Up([string] $Mode) {
    $engineName = Resolve-Engine
    if (-not (Test-Path -LiteralPath $VenvPython)) { Stop-WithError "Python environment missing. Run '.\startup.ps1 init' first." }

    if ($Mode -eq "dev-auth") {
        Initialize-EnvFile
        Import-EnvFile
        if (-not $env:OIDC_ISSUER) { Write-Warn "OIDC_ISSUER is not set: login will return 503. Check $EnvFile." }
        $issuer = $env:OIDC_ISSUER
        if (-not $issuer) { $issuer = "<unset>" }
        Write-Step "Auth ENABLED (OIDC issuer: $issuer)"
    } else {
        Import-EnvFile
        # Quick single-user dev: explicit bypass of the OIDC flow. The
        # backend's startup guard refuses this on a non-loopback bind.
        $env:AUTH_ENABLED = "false"
        Write-Step "Auth DISABLED (single-user dev bypass; loopback bind only)"
    }

    Assert-BackendPortFree
    Initialize-SearxngConfig

    # Keycloak must NOT start with Postgres: it needs the `keycloak` schema,
    # created once Postgres is ready (Initialize-KeycloakSchema).
    $services = @($PostgresService, "searxng")
    Write-Step "Starting containers ($engineName): $($services -join ' ')"
    if ((Invoke-Compose $engineName (@("up", "-d") + $services)) -ne 0) { Stop-WithError "Could not start the containers." }
    try {
        Wait-Postgres $engineName
        if ($Mode -eq "dev-auth") {
            Initialize-KeycloakSchema $engineName
            Write-Step "Starting containers ($engineName): $KeycloakService"
            if ((Invoke-Compose $engineName @("up", "-d", $KeycloakService)) -ne 0) { Stop-WithError "Could not start Keycloak." }
            Wait-Keycloak
        }

        Write-Step "Starting Neural Voice Server (run.py)"
        New-Item -ItemType Directory -Force -Path (Split-Path $RunnerPidFile) | Out-Null
        # Same console, no new window: Ctrl-C reaches run.py too, and uvicorn
        # shuts down cleanly on it.
        $runner = Start-Process -FilePath (Resolve-Path -LiteralPath $VenvPython) -ArgumentList "run.py" `
            -NoNewWindow -PassThru
        Write-TextFile $RunnerPidFile "$($runner.Id)"
        if ($Mode -eq "dev-auth") {
            Write-Step "Ready: sign in at http://localhost:5173 (Keycloak user: admin-user / password)"
            Write-Step "Add a second user at http://localhost:18080/admin (admin/admin) to test the approval flow: see deploy/README.md"
        } else {
            Write-Step "Ready: frontend: npm run dev (then http://localhost:5173)"
        }
        # Short waits, not one blocking WaitForExit(): PowerShell only notices
        # Ctrl-C between statements.
        while (-not $runner.WaitForExit(500)) { }
    } finally {
        # Runs on Ctrl-C, on an error, and when run.py exits by itself (e.g.
        # the port was taken after all): nothing is left running.
        Stop-Runner
        Write-Step "Stopping containers ($engineName)"
        Invoke-Compose $engineName @("down") | Out-Null
    }
}

function Invoke-Down {
    $engineName = Resolve-Engine
    Stop-Runner
    Write-Step "Stopping containers ($engineName)"
    if ((Invoke-Compose $engineName @("down")) -ne 0) { Stop-WithError "Could not stop the containers." }
}

function Invoke-Main([string] $Name, [string] $Arg) {
    switch ($Name) {
        "init" { Invoke-Init $Arg }
        "up" { Invoke-Up "" }
        "up-with-dev-auth" { Invoke-Up "dev-auth" }
        "down" { Invoke-Down }
        { $_ -in @("help", "-h", "--help", "/?") } { Show-Usage }
        default { Show-Usage; Stop-WithError "Unknown command: $Name" }
    }
}

# Run only when executed directly; dot-sourcing (. .\startup.ps1) loads the
# functions for tests.
if ($MyInvocation.InvocationName -ne ".") {
    Invoke-Main $Command $Engine
}
