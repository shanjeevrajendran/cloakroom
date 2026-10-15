# First-start installer for Windows; same stages and installer order as bootstrap.sh (see there):
# uv, pipx, else a private virtualenv in %USERPROFILE%\.cloakroom\venv (Python 3.10+ via the py launcher or python).
# bootstrap.ps1 -Check only says whether an installer exists (exit 0) and installs nothing.
param([switch]$Check)
if ($Check) {
  $can = (Get-Command uv -ErrorAction SilentlyContinue) -or (Get-Command pipx -ErrorAction SilentlyContinue) -or
    (Get-Command py -ErrorAction SilentlyContinue) -or (Get-Command python -ErrorAction SilentlyContinue)
  if ($can) { exit 0 } else { exit 1 }
}
$ErrorActionPreference = "Continue"
$H = if ($env:CLOAKROOM_HOME) { $env:CLOAKROOM_HOME } else { Join-Path $env:USERPROFILE ".cloakroom" }
New-Item -ItemType Directory -Force $H | Out-Null
$Lock = Join-Path $H "bootstrap.lock"; $State = Join-Path $H "bootstrap.state"; $Log = Join-Path $H "bootstrap.log"
if (Test-Path $Lock) {
  if (((Get-Date) - (Get-Item $Lock).LastWriteTime).TotalMinutes -lt 30) { exit 0 }
  Remove-Item -Recurse -Force $Lock
}
New-Item -ItemType Directory $Lock -ErrorAction Stop | Out-Null

function Find-Py310 {  # a Python 3.10+ command line, or $null
  foreach ($c in @(@('py', '-3'), @('python'))) {
    if (Get-Command $c[0] -ErrorAction SilentlyContinue) {
      & $c[0] @($c[1..($c.Length - 1)] | Where-Object { $_ }) -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>$null
      if ($LASTEXITCODE -eq 0) { return $c }
    }
  }
  return $null
}

$Venv = Join-Path $H "venv"
$Who = $env:CLOAKROOM_INSTALLER
if (-not $Who) {
  if (Get-Command uv -ErrorAction SilentlyContinue) { $Who = "uv" }
  elseif (Get-Command pipx -ErrorAction SilentlyContinue) { $Who = "pipx" }
  elseif (Find-Py310) { $Who = "venv" }
  else { $Who = "none" }
}

function Install-Spec([string]$Spec, [bool]$Force) {
  switch ($Who) {
    "uv"   { if ($Force) { & uv tool install --force $Spec *>> $Log } else { & uv tool install $Spec *>> $Log } }
    "pipx" { if ($Force) { & pipx install --force $Spec *>> $Log } else { & pipx install $Spec *>> $Log } }
    "venv" {
      $vpy = Join-Path $Venv "Scripts\python.exe"
      if (-not (Test-Path $vpy)) {
        $py = Find-Py310
        & $py[0] @($py[1..($py.Length - 1)] | Where-Object { $_ }) -m venv $Venv *>> $Log
      }
      $env:PIP_DISABLE_PIP_VERSION_CHECK = "1"; & $vpy -m pip install -q -U $Spec *>> $Log
    }
    default { $global:LASTEXITCODE = 1 }
  }
  return ($LASTEXITCODE -eq 0)
}

function Find-Cloakroom {
  $c = (Get-Command cloakroom -ErrorAction SilentlyContinue).Source
  if ($c) { return $c }
  $cands = @()
  if ($Who -eq "uv") { $cands += (Join-Path (& uv tool dir --bin 2>$null) "cloakroom.exe") }
  $cands += (Join-Path $env:USERPROFILE ".local\bin\cloakroom.exe"), (Join-Path $Venv "Scripts\cloakroom.exe")
  foreach ($p in $cands) { if ($p -and (Test-Path $p)) { return $p } }
  return $null
}

try {
  $Rules = if ($env:CLOAKROOM_SPEC_RULES) { $env:CLOAKROOM_SPEC_RULES } else { "cloakroom==0.4.1" }
  $Full = if ($env:CLOAKROOM_SPEC_FULL) { $env:CLOAKROOM_SPEC_FULL } else { "cloakroom[model,torch,ocr]==0.4.1" }
  # stage 2 again without local OCR if its wheels fail, so a broken OCR wheel never blocks the model
  $NoOcr = if ($env:CLOAKROOM_SPEC_NO_OCR) { $env:CLOAKROOM_SPEC_NO_OCR } else { "cloakroom[model,torch]==0.4.1" }
  "$(Get-Date -Format s) installer: $Who" | Add-Content $Log
  if ($Who -eq "none") {
    Set-Content $State "failed"
    "no installer found: install uv (https://docs.astral.sh/uv/) or Python 3.10+, then restart Claude Code" | Add-Content $Log
    exit 1
  }
  "$(Get-Date -Format s) stage 1 of 2: installing $Rules (rules-only protection)" | Add-Content $Log
  Set-Content $State "rules"
  if (-not (Install-Spec $Rules $false)) { Set-Content $State "failed"; "stage 1 failed" | Add-Content $Log; exit 1 }
  Set-Content $State "model"
  "$(Get-Date -Format s) stage 2 of 2: installing the model ($Full; about 1 GB on a Mac, up to about 4 GB elsewhere, of which local OCR about 100 MB)" | Add-Content $Log
  $ok = Install-Spec $Full $true
  if (-not $ok) {
    "$(Get-Date -Format s) the install with OCR failed: retrying without it ($NoOcr); scanned PDFs and photos stay held back" | Add-Content $Log
    $ok = Install-Spec $NoOcr $true
  }
  if ($ok) {
    $cr = Find-Cloakroom
    if ($cr) {
      "$(Get-Date -Format s) building the fast CPU model if this machine has no GPU (a few minutes)" | Add-Content $Log
      & $cr export-onnx --if-no-gpu *>> $Log
      if ($LASTEXITCODE -ne 0) { "CPU model build failed: full mode still works, just slower" | Add-Content $Log }
    }
    Remove-Item -Force $State; "$(Get-Date -Format s) done: full protection" | Add-Content $Log
    # start the service now, so the user's next prompt doesn't wait for the model's first load
    if ($cr) { Start-Process $cr -ArgumentList "serve" -WindowStyle Hidden | Out-Null; "$(Get-Date -Format s) service starting" | Add-Content $Log }
  }
  else { Set-Content $State "model-failed"; "stage 2 failed: staying on rules-only protection" | Add-Content $Log }
} finally { Remove-Item -Recurse -Force $Lock -ErrorAction SilentlyContinue }
