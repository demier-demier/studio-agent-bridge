# Install the example in an isolated native Windows x64 Python environment.
[CmdletBinding()]
param(
    [string]$PythonCommand = "py",
    [string]$EnvironmentPath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$ExampleRoot = Split-Path -Parent $PSScriptRoot
if (-not $EnvironmentPath) { $EnvironmentPath = Join-Path $ExampleRoot ".venv" }
$EnvironmentPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($EnvironmentPath)

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable exited with code $LASTEXITCODE" }
}

$PythonArguments = @()
if ([System.IO.Path]::GetFileNameWithoutExtension($PythonCommand) -eq "py") {
    $PythonArguments = @("-3.12")
}
$CheckPython = "import platform,struct,sys; assert sys.platform == 'win32' and struct.calcsize('P') == 8 and platform.machine().lower() in ('amd64','x86_64') and sys.version_info[:2] == (3,12), 'Use native Windows x64 Python 3.12'; print(sys.version)"
Invoke-Checked $PythonCommand ($PythonArguments + @("-c", $CheckPython))
$VenvPython = Join-Path $EnvironmentPath "Scripts/python.exe"
if (Test-Path -LiteralPath $EnvironmentPath) {
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        throw "Environment path already exists and is not a Windows Python venv: $EnvironmentPath"
    }
} else {
    Invoke-Checked $PythonCommand ($PythonArguments + @("-m", "venv", $EnvironmentPath))
}
Invoke-Checked $VenvPython @("-c", $CheckPython)
Invoke-Checked $VenvPython @("-m", "pip", "install", "--only-binary=:all:", "-r", (Join-Path $ExampleRoot "requirements.txt"))
Write-Host "Setup complete. No drivers, Blender, system packages or execution policies were changed."
Write-Host "Next: .\windows\run.ps1 -Vendor NVIDIA -Preset smoke"
Write-Host "For AMD use -Vendor AMD; for an explicit CPU check use -Vendor CPU."
