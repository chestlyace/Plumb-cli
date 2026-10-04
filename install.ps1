# Install Plumb on Windows. In PowerShell:
#   irm https://raw.githubusercontent.com/chestlyace/Plumb-cli/main/install.ps1 | iex
# Then open a new terminal and run `tutor` inside a project; the first run sets up
# Ollama and the model.

$ErrorActionPreference = "Stop"
$Source = "https://github.com/chestlyace/Plumb-cli/archive/refs/heads/main.zip"

Write-Host "Installing Plumb..." -ForegroundColor Cyan

# 1. uv, the Python tool installer (it also fetches the right Python for Plumb).
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..."
    powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv didn't install. See https://docs.astral.sh/uv/getting-started/installation/"
    }
}

# 2. Plumb itself, from the repo's zip (no git needed).
uv tool install --force --refresh --python 3.14 $Source
if ($LASTEXITCODE -ne 0) { throw "Installing Plumb failed (see the messages above)." }

# 3. Make `tutor` available in new terminals.
uv tool update-shell | Out-Null

Write-Host ""
Write-Host "Plumb is installed." -ForegroundColor Green
Write-Host "Open a NEW terminal, go to one of your projects, and run:  tutor"
Write-Host "The first run sets up Ollama and the model for you."
