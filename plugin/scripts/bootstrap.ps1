# First-start installer for Windows; same stages and installer order as bootstrap.sh (see there):
# uv, pipx, else uv is installed for you (Astral's installer for the pinned uv version, run only when its SHA-256
# matches), else a private virtualenv in %USERPROFILE%\.cloakroom\venv (Python 3.10+ via the py launcher or python).
# On failure %USERPROFILE%\.cloakroom\bootstrap.reason says why (offline | checksum | failed) for the hook's message.
# msvc-runtime (PyPI) ships the Visual C++ runtime DLLs torch needs, so no admin install of the redistributable is required.
# Every route ends with cloakroom.exe in %USERPROFILE%\.local\bin and that folder on the User PATH.
# bootstrap.ps1 -Check exits 0 and installs nothing (the install itself fails and is retried when offline).
# bootstrap.ps1 -Upgrade: the plugin was updated; same as bootstrap.sh --upgrade (see there), but a clean reinstall:
# the hooks are held (bootstrap.lock\upgrade), the service is stopped, it waits (up to 60 s) until nothing runs from
# the environment, uninstalls, installs the pinned release with the same extras, and starts the service again. The
# stamp is written only once the installed program's --version works.
param([switch]$Check, [switch]$Upgrade)
if ($Check) { exit 0 }  # Windows can always try: uv is fetched when no installer exists, and a failed install retries later
$ErrorActionPreference = "Continue"
$H = if ($env:CLOAKROOM_HOME) { $env:CLOAKROOM_HOME } else { Join-Path $env:USERPROFILE ".cloakroom" }
New-Item -ItemType Directory -Force $H | Out-Null
$Lock = Join-Path $H "bootstrap.lock"; $State = Join-Path $H "bootstrap.state"; $Log = Join-Path $H "bootstrap.log"
$ReasonFile = Join-Path $H "bootstrap.reason"
# bootstrap.log is UTF-8 without a byte-order mark: Windows PowerShell 5.1 would write redirected output as UTF-16,
# and reads native programs' output (uv) in the console's code page unless told it is UTF-8
$Utf8 = New-Object System.Text.UTF8Encoding $false
try { [Console]::OutputEncoding = $Utf8 } catch { }
function Out-Log { process { [IO.File]::AppendAllText($Log, "$_`r`n", $Utf8) } }
$Pin = "0.6.6"  # the release this plugin version installs (scripts/gen_hooks.py bakes the same into the hooks)
$Stamp = Join-Path $H "upgrade.stamp"; $Notice = Join-Path $H "upgrade.notice"; $Probe = Join-Path $PSScriptRoot "upgrade_probe.py"
$UvVersion = "0.13.1"
$UvPs1Url = "https://astral.sh/uv/$UvVersion/install.ps1"
# SHA-256 of that script: the digest GitHub lists for uv-installer.ps1 in the uv 0.13.1 release
$UvPs1Sha256 = "f6c934a9926096f957615f9f5276efd3caeab58253eec613963a72d12b380e63"
$script:Reason = ""
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

function Install-Uv {  # Astral's installer for the pinned uv, run only if its SHA-256 matches; uv.exe's path, or $null
  "$(Get-Date -Format s) no uv or pipx: fetching uv $UvVersion ($UvPs1Url)" | Out-Log
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ("cloakroom-uv-install-" + [guid]::NewGuid().ToString("N") + ".ps1")
  try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $UvPs1Url -OutFile $tmp -TimeoutSec 300 -ErrorAction Stop
  } catch {
    # no HTTP response at all = no connection; an HTTP error status = something else
    $script:Reason = if ($_.Exception.Response) { "failed" } else { "offline" }
    "could not download the uv installer: $_" | Out-Log
    Remove-Item -Force $tmp -ErrorAction SilentlyContinue
    return $null
  }
  $got = (Get-FileHash -Algorithm SHA256 $tmp).Hash.ToLower()
  if ($got -ne $UvPs1Sha256) {
    $script:Reason = "checksum"
    "uv installer checksum mismatch (got $got, want $UvPs1Sha256): not running it" | Out-Log
    Remove-Item -Force $tmp -ErrorAction SilentlyContinue
    return $null
  }
  try { & powershell -NoProfile -ExecutionPolicy Bypass -File $tmp 2>&1 | Out-Log } catch { "uv install error: $_" | Out-Log }
  Remove-Item -Force $tmp -ErrorAction SilentlyContinue
  if (Test-Path $UvExe) { "uv $UvVersion installed: $UvExe" | Out-Log; return $UvExe }
  $script:Reason = "failed"
  return $null
}

function Write-Steps([string]$Why) {  # numbered steps for the user (the hook shows the same: scripts/gen_hooks.py)
  $restart = "Then restart Claude Code (/exit, then claude)"
  $steps = switch ($Why) {
    "offline"  { "To finish: 1. Connect to the internet (behind a proxy or firewall, allow astral.sh, releases.astral.sh, pypi.org, files.pythonhosted.org and huggingface.co). 2. $restart; it also retries by itself in 10 minutes. Log: $Log" }
    "checksum" { "To finish: 1. Install uv yourself: winget install astral-sh.uv (or see https://docs.astral.sh/uv/getting-started/installation/). 2. $restart. Log: $Log" }
    "model"    { "To finish the model install: 1. Check the internet connection and free disk space (up to about 4 GB). 2. Run: cloakroom doctor, and fix what it reports. 3. Delete $State. 4. $restart. Rules-only protection stays on until then. Log: $Log" }
    default    { "To finish: 1. Read the error above in this log. 2. Fix it, or install Cloakroom yourself (command: https://github.com/shanjeevrajendran/cloakroom#get-started). 3. $restart. Log: $Log" }
  }
  $steps | Out-Log
}

function Stop-Failed([string]$Why, [string]$What) {
  Set-Content $State "failed"; Set-Content $ReasonFile $Why
  "$(Get-Date -Format s) $What" | Out-Log
  Write-Steps $Why
  exit 1
}

function Write-Plain([string]$Path, [string]$Text) {  # no byte-order mark, no CRLF: hook.sh (Git Bash) reads these too
  [IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding $false))
}

function Get-EnvProcess([string[]]$Marks) {  # processes whose program or command line names one of $Marks
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $t = ("" + $_.ExecutablePath + " " + $_.CommandLine).ToLower()
    $_.ProcessId -ne $PID -and @($Marks | Where-Object { $_ -and $t.Contains($_.ToLower()) }).Count -gt 0
  }
}

function Stop-CloakroomService([string[]]$Marks) {  # stop the `serve` processes among them; how many
  $n = 0
  foreach ($p in @(Get-EnvProcess $Marks | Where-Object { $_.CommandLine -match '\sserve"?\s*$' })) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue; $n++
  }
  return $n
}

function Wait-Idle([string[]]$Marks, [int]$Seconds = 60) {  # until nothing runs from the environment (two quiet looks)
  $end = (Get-Date).AddSeconds($Seconds); $quiet = 0
  while ((Get-Date) -lt $end) {
    if (@(Get-EnvProcess $Marks).Count -eq 0) { $quiet++; if ($quiet -ge 2) { return $true } } else { $quiet = 0 }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

# While the files are swapped, the hooks answer for the program (tool calls held) and never start it
function Set-Hold { Write-Plain (Join-Path $Lock "upgrade") "" }
function Clear-Hold { Remove-Item -Force (Join-Path $Lock "upgrade") -ErrorAction SilentlyContinue }

function Remove-Install([string]$Route, [string]$Prefix, [string]$Uv) {  # take away a broken program of ours
  switch ($Route) {
    "uv"   { & $Uv tool uninstall cloakroom 2>&1 | Out-Log }
    "pipx" { & pipx uninstall cloakroom 2>&1 | Out-Log }
    "venv" { Remove-Item -Recurse -Force $Venv -ErrorAction SilentlyContinue }
  }
  if ($Route -ne "venv" -and $LASTEXITCODE -ne 0 -and $Prefix) { Remove-Item -Recurse -Force $Prefix -ErrorAction SilentlyContinue }
  foreach ($f in @("cloakroom.exe", "cloakroom.cmd")) {  # what uv, pipx or Publish-Cloakroom put in ~\.local\bin
    $p = Join-Path $LocalBin $f
    if (Test-Path $p) {
      & $p --version *> $null
      if ($LASTEXITCODE -ne 0) { Remove-Item -Force $p -ErrorAction SilentlyContinue }
    }
  }
}

function Install-Clean([string]$Route, [string]$Spec, [string]$Uv) {  # a fresh install, no --force (nothing to replace)
  switch ($Route) {
    "uv"   { & $Uv tool install --python 3.12 --with msvc-runtime $Spec 2>&1 | Out-Log }
    "pipx" { & pipx install --preinstall msvc-runtime $Spec 2>&1 | Out-Log }
    "venv" {
      $py = Find-Py310
      if (-not $py) { return $false }
      & $py[0] @($py[1..($py.Length - 1)] | Where-Object { $_ }) -m venv $Venv 2>&1 | Out-Log
      $env:PIP_DISABLE_PIP_VERSION_CHECK = "1"; & (Join-Path $Venv "Scripts\python.exe") -m pip install -q $Spec msvc-runtime 2>&1 | Out-Log
    }
    default { return $false }
  }
  return ($LASTEXITCODE -eq 0)
}

function Invoke-Upgrade {  # the plugin moved to $Pin: bring an older install up to it (see bootstrap.sh --upgrade)
  # returns 0 or 1 when finished, 2 when a broken install was removed and the first install must run
  $cr = (Get-Command cloakroom -ErrorAction SilentlyContinue).Source
  foreach ($c in @((Join-Path $LocalBin "cloakroom.exe"), (Join-Path $Venv "Scripts\cloakroom.exe"))) {
    if (-not $cr -and (Test-Path $c)) { $cr = $c }
  }
  if (-not $cr) { return 0 }  # nothing installed: the hook starts the first install instead
  $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
  if (-not $uv -and (Test-Path $UvExe)) { $uv = $UvExe }
  $envs = @()  # route, environment folder
  if ($uv) { $d = ((& $uv tool dir 2>$null) | Select-Object -First 1); if ($d) { $envs += , @("uv", (Join-Path $d "cloakroom")) } }
  if (Get-Command pipx -ErrorAction SilentlyContinue) {
    $d = ((& pipx environment --value PIPX_LOCAL_VENVS 2>$null) | Select-Object -First 1)
    if ($d) { $envs += , @("pipx", (Join-Path $d "cloakroom")) }
  }
  $envs += , @("venv", $Venv)
  # the Python of the program's environment: next to it (venv, conda, system pip), else the uv/pipx/venv one it
  # was copied from into ~\.local\bin
  $py = $null; $dir = Split-Path $cr
  $cands = @((Join-Path $dir "python.exe"), (Join-Path (Split-Path $dir) "python.exe"))
  foreach ($e in $envs) { if ($cr.ToLower().StartsWith($e[1].ToLower() + "\")) { $cands = @(Join-Path $e[1] "Scripts\python.exe") + $cands } }
  if ($dir.TrimEnd('\') -eq $LocalBin.TrimEnd('\')) { foreach ($e in $envs) { $cands += Join-Path $e[1] "Scripts\python.exe" } }
  foreach ($p in $cands) { if (-not $py -and (Test-Path $p)) { $py = $p } }
  $line = if ($py -and (Test-Path $Probe)) { (& $py -I $Probe $Pin 2>$null) | Select-Object -First 1 } else { "" }
  $f = "$line" -split " ", 4
  $kind, $ver, $ext, $prefix = if ($f.Length -ge 4) { $f } else { @("broken", "", "-", "") }
  if (-not $prefix -or $prefix -eq "-") { $prefix = if ($py) { Split-Path (Split-Path $py) } else { "" } }
  # a program that doesn't run (its environment lost cloakroom, or its Python) is broken, never "done"
  if ($kind -eq "missing") { $kind = "broken" }
  if ($kind -ne "broken") { & $cr --version *> $null; if ($LASTEXITCODE -ne 0) { $kind = "broken" } }
  if ($kind -in @("same", "newer", "dev")) {
    Write-Plain $Stamp $Pin
    "$(Get-Date -Format s) upgrade check: cloakroom $ver ($kind) left as is for plugin $Pin" | Out-Log; return 0
  }
  if ($ext -eq "-") { $ext = "" }
  $spec = if ($env:CLOAKROOM_SPEC_UPGRADE) { $env:CLOAKROOM_SPEC_UPGRADE } elseif ($ext) { "cloakroom[$ext]==0.6.6" } else { "cloakroom==0.6.6" }
  $route = "unknown"
  if ($prefix) { foreach ($e in $envs) { if ($prefix.TrimEnd('\').ToLower() -eq $e[1].TrimEnd('\').ToLower()) { $route = $e[0]; break } } }
  $marks = @($prefix, $cr, (Join-Path $LocalBin "cloakroom"))
  if ($kind -eq "broken" -and $route -eq "unknown") {  # not ours to remove: the user reinstalls it
    Write-Plain $Stamp "failed:$Pin"
    $msg = "cloakroom: the installed program ($cr) does not run, and it wasn't installed by uv, pipx or the plugin, so it can't repair itself. To finish: 1. Run: uv tool install --force --python 3.12 `"cloakroom==0.6.6`" (or reinstall it the way you installed it). 2. Then restart Claude Code (/exit, then claude)."
    Write-Plain $Notice ((@{ systemMessage = $msg } | ConvertTo-Json -Compress) + "`n")
    "$(Get-Date -Format s) upgrade check: $cr does not run and was not installed by uv, pipx or this script" | Out-Log
    $msg | Out-Log; return 1
  }
  if ($route -eq "unknown") {  # installed some other way: the user upgrades it, with these steps, once
    $msg = "cloakroom: version $ver is installed, but the plugin now uses $Pin, and this copy wasn't installed by uv, pipx or the plugin, so it can't upgrade itself. To finish: 1. Run: & `"$py`" -m pip install -U `"$spec`" (or, with uv: uv tool install --force --python 3.12 `"$spec`"). 2. Then restart Claude Code (/exit, then claude). Until then version $ver keeps protecting you."
    Write-Plain $Notice ((@{ systemMessage = $msg } | ConvertTo-Json -Compress) + "`n")
    Write-Plain $Stamp $Pin
    "$(Get-Date -Format s) upgrade check: cloakroom $ver in $prefix is older than $Pin but not installed by uv, pipx or this script: steps shown at the next session start" | Out-Log
    $msg | Out-Log; return 0
  }
  # Windows can't replace files in use: hold the hooks, stop the service, wait until nothing runs from the
  # environment (a hook call in flight finishes within seconds), then swap
  Set-Hold
  $was = Stop-CloakroomService $marks
  if (-not (Wait-Idle $marks 60)) {
    Clear-Hold
    if ($was -gt 0 -and $kind -ne "broken") { Start-Process $cr -ArgumentList "serve" -WindowStyle Hidden | Out-Null }
    Write-Plain $Stamp "failed:$Pin"
    "$(Get-Date -Format s) upgrade postponed: programs from $prefix were still running after 60 s; it tries again in 10 minutes" | Out-Log
    return 1
  }
  if ($kind -eq "broken") {
    "$(Get-Date -Format s) upgrade check: $cr does not run ($(if ($ver) { $ver } else { 'no version' })): removing it ($route) and installing again" | Out-Log
    Remove-Install $route $prefix $uv; Clear-Hold; Remove-Item -Force $Stamp -ErrorAction SilentlyContinue; return 2
  }
  # an in-place `uv tool install --force` on Windows has left environments without the cloakroom package, so the
  # upgrade is a clean reinstall: remove, then install the pinned release with the same extras
  "$(Get-Date -Format s) upgrading $ver -> $Pin (clean reinstall on Windows; $route`: $spec)" | Out-Log
  Remove-Install $route $prefix $uv
  $ok = Install-Clean $route $spec $uv
  $cr2 = (Get-Command cloakroom -ErrorAction SilentlyContinue).Source
  foreach ($c in @((Join-Path $LocalBin "cloakroom.exe"), (Join-Path $Venv "Scripts\cloakroom.exe"))) {
    if (-not ($cr2 -and (Test-Path $cr2)) -and (Test-Path $c)) { $cr2 = $c }
  }
  $new = $null
  if ($cr2 -and (Test-Path $cr2)) { $new = ((& $cr2 --version 2>$null) | Select-Object -First 1); if ($LASTEXITCODE -ne 0) { $new = $null } }
  if (-not $new) {  # nothing that runs: take it away, so the hook denies tool calls with the steps and retries
    "$(Get-Date -Format s) the reinstall $(if ($ok) { 'finished, but the program does not run' } else { 'failed' })" | Out-Log
    Remove-Install $route $prefix $uv; Clear-Hold; Remove-Item -Force $Stamp -ErrorAction SilentlyContinue
    Stop-Failed "failed" "upgrade to $Pin failed: Cloakroom is not installed now; it tries again in 10 minutes"
  }
  Clear-Hold
  if ($was -gt 0) {
    Start-Process $cr2 -ArgumentList "serve" -WindowStyle Hidden | Out-Null
    "$(Get-Date -Format s) service restarted on the new version" | Out-Log
  }
  Write-Plain $Stamp $Pin
  "$(Get-Date -Format s) done: $new" | Out-Log
  return 0
}

if ($Upgrade) {
  $rc = 1
  try { $rc = Invoke-Upgrade } catch { "upgrade error: $_" | Out-Log; $rc = 1 }
  finally {
    Remove-Item -Force (Join-Path $Lock "upgrade") -ErrorAction SilentlyContinue
    if ($rc -ne 2) { Remove-Item -Recurse -Force $Lock -ErrorAction SilentlyContinue }
  }
  if ($rc -ne 2) { exit $rc }
  $Upgrade = $false  # a broken install was removed: the first install, below, keeps the lock
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
    "uv"   { if ($Force) { & $UvExe tool install --python 3.12 --with msvc-runtime --force $Spec 2>&1 | Out-Log } else { & $UvExe tool install --python 3.12 --with msvc-runtime $Spec 2>&1 | Out-Log } }
    "pipx" { if ($Force) { & pipx install --preinstall msvc-runtime --force $Spec 2>&1 | Out-Log } else { & pipx install --preinstall msvc-runtime $Spec 2>&1 | Out-Log } }
    "venv" {
      $vpy = Join-Path $Venv "Scripts\python.exe"
      if (-not (Test-Path $vpy)) {
        $py = Find-Py310
        & $py[0] @($py[1..($py.Length - 1)] | Where-Object { $_ }) -m venv $Venv 2>&1 | Out-Log
      }
      $env:PIP_DISABLE_PIP_VERSION_CHECK = "1"; & $vpy -m pip install -q -U $Spec msvc-runtime 2>&1 | Out-Log
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
      "$(Get-Date -Format s) added $LocalBin to your user PATH" | Out-Log
    }
  } catch { "could not publish cloakroom to ${LocalBin}: $_" | Out-Log }
}

$modelBroken = $false
Remove-Item -Force $ReasonFile -ErrorAction SilentlyContinue
try {
  $Rules = if ($env:CLOAKROOM_SPEC_RULES) { $env:CLOAKROOM_SPEC_RULES } else { "cloakroom==0.6.6" }
  $Full = if ($env:CLOAKROOM_SPEC_FULL) { $env:CLOAKROOM_SPEC_FULL } else { "cloakroom[model,torch,ocr]==0.6.6" }
  # stage 2 again without local OCR if its wheels fail, so a broken OCR wheel never blocks the model
  $NoOcr = if ($env:CLOAKROOM_SPEC_NO_OCR) { $env:CLOAKROOM_SPEC_NO_OCR } else { "cloakroom[model,torch]==0.6.6" }
  "$(Get-Date -Format s) installer: $Who" | Out-Log
  if ($Who -eq "none") {
    $why = if ($script:Reason) { $script:Reason } else { "failed" }
    Stop-Failed $why "no installer: uv could not be fetched and there is no pipx or Python 3.10+"
  }
  "$(Get-Date -Format s) stage 1 of 2: installing $Rules (rules-only protection)" | Out-Log
  Set-Content $State "rules"
  if (-not (Install-Spec $Rules $false)) { Stop-Failed "failed" "stage 1 failed" }
  $cr1 = Find-Cloakroom
  $runs = $false
  if ($cr1) { & $cr1 --version *> $null; $runs = ($LASTEXITCODE -eq 0) }
  if (-not $runs) {
    $uv1 = if ($Who -eq "uv") { $UvExe } else { $null }
    Remove-Install $Who "" $uv1
    Stop-Failed "failed" "stage 1 installed cloakroom, but the program does not run ($(if ($cr1) { $cr1 } else { 'not found' }))"
  }
  Set-Content $State "model"
  "$(Get-Date -Format s) stage 2 of 2: installing the model ($Full; about 1 GB on a Mac, up to about 4 GB elsewhere, of which local OCR about 100 MB)" | Out-Log
  $ok = Install-Spec $Full $true
  if (-not $ok) {
    "$(Get-Date -Format s) the install with OCR failed: retrying without it ($NoOcr); scanned PDFs and photos stay held back" | Out-Log
    $ok = Install-Spec $NoOcr $true
  }
  if ($ok) {
    # stage 2 replaced the program in place: if that left one that doesn't run, fall back to a clean rules-only
    # install (held, like an upgrade), so a broken program is never left behind or stamped
    $cr = Find-Cloakroom
    $runs = $false
    if ($cr) { & $cr --version *> $null; $runs = ($LASTEXITCODE -eq 0) }
    if (-not $runs) {
      "$(Get-Date -Format s) stage 2 left a program that does not run: reinstalling rules-only" | Out-Log
      Set-Hold
      [void](Wait-Idle @((Join-Path $LocalBin "cloakroom"), $cr) 60)
      Remove-Install $Who "" $(if ($Who -eq "uv") { $UvExe } else { $null })
      $re = Install-Clean $Who $Rules $UvExe
      Clear-Hold
      $cr = Find-Cloakroom
      if ($cr) { & $cr --version *> $null; $re = $re -and ($LASTEXITCODE -eq 0) } else { $re = $false }
      if (-not $re) { Remove-Install $Who "" $(if ($Who -eq "uv") { $UvExe } else { $null }); Stop-Failed "failed" "the rules-only reinstall failed" }
      Write-Plain $Stamp $Pin
      Set-Content $State "model-failed"; "$(Get-Date -Format s) stage 2 failed: staying on rules-only protection" | Out-Log; Write-Steps "model"; exit 1
    }
    Publish-Cloakroom $cr
    if ($cr) {
      "$(Get-Date -Format s) building the fast CPU model if this machine has no GPU (a few minutes)" | Out-Log
      & $cr export-onnx --if-no-gpu 2>&1 | Out-Log
      if ($LASTEXITCODE -ne 0) {
        # the fast CPU model is optional only if the model itself loads: ask doctor before claiming that
        & $cr doctor 2>&1 | Out-Log
        if ($LASTEXITCODE -eq 0) { "CPU model build failed: full mode still works, just slower" | Out-Log }
        else { $modelBroken = $true; "CPU model build failed and the model can't load on this machine (see the doctor lines above)" | Out-Log }
      }
    }
    Write-Plain $Stamp $Pin  # this plugin version's release is in, and runs: no upgrade check on the next hook call
    if ($modelBroken) { Set-Content $State "model-failed"; "$(Get-Date -Format s) stage 2 failed: staying on rules-only protection" | Out-Log; Write-Steps "model"; exit 1 }
    Remove-Item -Force $State; "$(Get-Date -Format s) done: full protection. Program: $(if ($cr) { $cr } else { Join-Path $LocalBin 'cloakroom.exe' }) (if 'cloakroom' is not found in a terminal, open a new terminal or use that full path)" | Out-Log
    # start the service now, so the user's next prompt doesn't wait for the model's first load
    if ($cr) { Start-Process $cr -ArgumentList "serve" -WindowStyle Hidden | Out-Null; "$(Get-Date -Format s) service starting" | Out-Log }
  }
  else {
    $cr = Find-Cloakroom
    if ($cr) { & $cr --version *> $null; if ($LASTEXITCODE -eq 0) { Write-Plain $Stamp $Pin } }
    Set-Content $State "model-failed"; "$(Get-Date -Format s) stage 2 failed: staying on rules-only protection" | Out-Log; Write-Steps "model"
  }
} finally { Remove-Item -Recurse -Force $Lock -ErrorAction SilentlyContinue }
