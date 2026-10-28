# Check the selected backend, run a new simulation, then audit its saved trajectory.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("NVIDIA", "AMD", "CPU")]
    [string]$Vendor,
    [ValidateSet("smoke", "video")]
    [string]$Preset = "smoke",
    [string]$OutputDirectory = "",
    [string]$Python = "",
    [ValidateRange(1, 32)]
    [int]$CpuThreads = 4
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$ExampleRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $ExampleRoot ".venv/Scripts/python.exe" }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Python not found. Run windows/setup.ps1 or provide -Python with an executable path." }
$Python = (Resolve-Path -LiteralPath $Python).Path
$Backend = @{ NVIDIA = "cuda"; AMD = "vulkan"; CPU = "cpu" }[$Vendor.ToUpperInvariant()]
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $ExampleRoot ("runs/{0}-{1}-{2}" -f $Vendor.ToLowerInvariant(), $Preset, (Get-Date -Format "yyyyMMdd-HHmmss-fff"))
}
$OutputDirectory = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
if (Test-Path -LiteralPath $OutputDirectory) { throw "Refusing to overwrite an existing run: $OutputDirectory" }

function Invoke-PythonChecked {
    param([string[]]$Arguments)
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python exited with code $LASTEXITCODE. The workflow stopped; no CPU fallback was requested." }
}

Write-Host "Requested simulation backend: $Backend. A GPU run requires the matching working driver."
Invoke-PythonChecked @((Join-Path $ExampleRoot "check_granular.py"), "--backend", $Backend)
Invoke-PythonChecked @((Join-Path $ExampleRoot "simulate.py"), "--preset", $Preset, "--arch", $Backend, "--cpu-threads", "$CpuThreads", "--output", $OutputDirectory)
$AuditArguments = @((Join-Path $ExampleRoot "locomotion/audit_run.py"), $OutputDirectory)
if ($Preset -eq "smoke") { $AuditArguments += @("--minimum-travel", "0") }
Invoke-PythonChecked $AuditArguments
Write-Host "Simulation and bounded trajectory audit completed: $OutputDirectory"
Write-Host "Rendering is a separate stage. See docs/windows.md."
