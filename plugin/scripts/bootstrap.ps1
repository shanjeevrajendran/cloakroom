# First-start installer for Windows; same stages and installer order as bootstrap.sh (see there):
# uv, pipx, else uv is installed for you (Astral's installer), else a private virtualenv in
# %USERPROFILE%\.cloakroom\venv (Python 3.10+ via the py launcher or python).
# msvc-runtime (PyPI) ships the Visual C++ runtime DLLs torch needs, so no admin install of the redistributable is required.
# Every route ends with cloakroom.exe in %USERPROFILE%\.local\bin and that folder on the User PATH.
# bootstrap.ps1 -Check exits 0 and installs nothing (the install itself fails and is retried when offline).
param([switch]$Check)
if ($Check) { exit 0 }  # Windows can always try: uv is fetched when no installer exists, and a failed install retries later
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
$LocalBin = Join-Path $env:USERPROFILE ".local\bin"
$UvExe = Join-Path $LocalBin "uv.exe"

function Install-Uv {  # Astral's installer; returns the full path of uv.exe, or $null
  "$(Get-Date -Format s) no uv or pipx: installing uv (https://astral.sh/uv)" | Add-Content $Log
  try { & powershell -NoProfile -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex" *>> $Log } catch { "uv install error: $_" | Add-Content $Log }
  if (Test-Path $UvExe) { return $UvExe }
  return $null
}

$Who = $env:CLOAKROOM_INSTALLER
if (-not $Who) {
  if (Get-Command uv -ErrorAction SilentlyContinue) { $Who = "uv"; $UvExe = (Get-Command uv).Source }
  elseif (Test-Path $UvExe) { $Who = "uv" }
  elseif (Get-Command pipx -ErrorAction SilentlyContinue) { $Who = "pipx" }
  elseif (Install-Uv) { $Who = "uv" }
  elseif (Find-Py310) { $Who = "venv" }
  else { $Who = "none" }
}
elseif ($Who -eq "uv" -and -not (Test-Path $UvExe)) { $c = Get-Command uv -ErrorAction SilentlyContinue; if ($c) { $UvExe = $c.Source } }

function Install-Spec([string]$Spec, [bool]$Force) {
  switch ($Who) {
    "uv"   { if ($Force) { & $UvExe tool install --python 3.12 --with msvc-runtime --force $Spec *>> $Log } else { & $UvExe tool install --python 3.12 --with msvc-runtime $Spec *>> $Log } }
    "pipx" { if ($Force) { & pipx install --preinstall msvc-runtime --force $Spec *>> $Log } else { & pipx install --preinstall msvc-runtime $Spec *>> $Log } }
    "venv" {
      $vpy = Join-Path $Venv "Scripts\python.exe"
      if (-not (Test-Path $vpy)) {
        $py = Find-Py310
        & $py[0] @($py[1..($py.Length - 1)] | Where-Object { $_ }) -m venv $Venv *>> $Log
      }
      $env:PIP_DISABLE_PIP_VERSION_CHECK = "1"; & $vpy -m pip install -q -U $Spec msvc-runtime *>> $Log
    }
    default { $global:LASTEXITCODE = 1 }
  }
  return ($LASTEXITCODE -eq 0)
}

function Find-Cloakroom {
  $c = (Get-Command cloakroom -ErrorAction SilentlyContinue).Source
  if ($c) { return $c }
  $cands = @()
  if ($Who -eq "uv") { $cands += (Join-Path (& $UvExe tool dir --bin 2>$null) "cloakroom.exe") }
  $cands += (Join-Path $env:USERPROFILE ".local\bin\cloakroom.exe"), (Join-Path $Venv "Scripts\cloakroom.exe")
  foreach ($p in $cands) { if ($p -and (Test-Path $p)) { return $p } }
  return $null
}

function Publish-Cloakroom([string]$Exe) {  # exe in ~\.local\bin and that folder on the User PATH
  try {
    $dst = Join-Path $LocalBin "cloakroom.exe"
    New-Item -ItemType Directory -Force $LocalBin | Out-Null
    if ($Exe -and -not (Test-Path $dst) -and -not (Test-Path (Join-Path $LocalBin "cloakroom.cmd"))) {
      try {
        Copy-Item $Exe $dst -ErrorAction Stop
        & $dst --version *> $null
        if ($LASTEXITCODE -ne 0) { throw "copied exe does not run" }
      } catch {
        Remove-Item -Force $dst -ErrorAction SilentlyContinue
        Set-Content (Join-Path $LocalBin "cloakroom.cmd") "@`"$Exe`" %*" -Encoding Ascii
      }
    }
    $want = $LocalBin.TrimEnd('\')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $has = @($user -split ';' | Where-Object { $_ } | ForEach-Object { $_.TrimEnd('\') }) -contains $want  # -contains is case-insensitive
    if (-not $has) {
      $new = if ($user) { $user.TrimEnd(';') + ';' + $LocalBin } else { $LocalBin }
      [Environment]::SetEnvironmentVariable('Path', $new, 'User')
      "$(Get-Date -Format s) added $LocalBin to your user PATH" | Add-Content $Log
    }
  } catch { "could not publish cloakroom to ${LocalBin}: $_" | Add-Content $Log }
}

$modelBroken = $false
try {
  $Rules = if ($env:CLOAKROOM_SPEC_RULES) { $env:CLOAKROOM_SPEC_RULES } else { "cloakroom==0.4.3" }
  $Full = if ($env:CLOAKROOM_SPEC_FULL) { $env:CLOAKROOM_SPEC_FULL } else { "cloakroom[model,torch,ocr]==0.4.3" }
  # stage 2 again without local OCR if its wheels fail, so a broken OCR wheel never blocks the model
  $NoOcr = if ($env:CLOAKROOM_SPEC_NO_OCR) { $env:CLOAKROOM_SPEC_NO_OCR } else { "cloakroom[model,torch]==0.4.3" }
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
    Publish-Cloakroom $cr
    if ($cr) {
      "$(Get-Date -Format s) building the fast CPU model if this machine has no GPU (a few minutes)" | Add-Content $Log
      & $cr export-onnx --if-no-gpu *>> $Log
      if ($LASTEXITCODE -ne 0) {
        # the fast CPU model is optional only if the model itself loads: ask doctor before claiming that
        & $cr doctor *>> $Log
        if ($LASTEXITCODE -eq 0) { "CPU model build failed: full mode still works, just slower" | Add-Content $Log }
        else { $modelBroken = $true; "CPU model build failed and the model can't load on this machine (see the doctor lines above)" | Add-Content $Log }
      }
    }
    if ($modelBroken) { Set-Content $State "model-failed"; "$(Get-Date -Format s) stage 2 failed: staying on rules-only protection" | Add-Content $Log; exit 1 }
    Remove-Item -Force $State; "$(Get-Date -Format s) done: full protection. Program: $(if ($cr) { $cr } else { Join-Path $LocalBin 'cloakroom.exe' }) (if 'cloakroom' is not found in a terminal, open a new terminal or use that full path)" | Add-Content $Log
    # start the service now, so the user's next prompt doesn't wait for the model's first load
    if ($cr) { Start-Process $cr -ArgumentList "serve" -WindowStyle Hidden | Out-Null; "$(Get-Date -Format s) service starting" | Add-Content $Log }
  }
  else { Set-Content $State "model-failed"; "stage 2 failed: staying on rules-only protection" | Add-Content $Log }
} finally { Remove-Item -Recurse -Force $Lock -ErrorAction SilentlyContinue }
