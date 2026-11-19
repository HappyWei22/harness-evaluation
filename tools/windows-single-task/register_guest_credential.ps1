$ErrorActionPreference = 'Stop'
$path = Get-HarnessGuestCredentialPath
$mode = $env:HARNESS_GUEST_CREDENTIAL_SETUP

function Confirm-HarnessGuestCredential([guid]$VmId, [pscredential]$Credential) {
    $profileMatches = Invoke-Command -VMId $VmId -Credential $Credential -ScriptBlock {
        $env:USERPROFILE -eq 'C:\Users\alex_'
    }
    if ($profileMatches -ne $true) { throw 'The credential does not connect to the registered guest profile.' }
}

if ($mode -notin @('register', 'refresh', 'verify')) { throw 'Unknown guest credential setup mode.' }
if ($mode -eq 'register' -and (Test-Path -LiteralPath $path)) { return }

$vm = Get-VM -Name 'Harness-DSH-Win11'
if ($vm.State -ne 'Running') { throw 'Start the registered Windows VM before checking credentials.' }
if ($mode -eq 'verify') {
    Confirm-HarnessGuestCredential $vm.Id (Get-HarnessGuestCredential -RequireSaved)
    Write-Host 'GuestCredentialReuse: PASS'
    Write-Host 'GuestProfileMatches: True'
    return
}

Write-Host 'Enter the Windows account password for the virtual machine.'
$credential = Get-Credential
if ($null -eq $credential) { throw 'Guest credential entry was cancelled.' }
Confirm-HarnessGuestCredential $vm.Id $credential

New-Item -ItemType Directory -Path (Split-Path -Parent $path) -Force | Out-Null
$temporary = "$path.$([guid]::NewGuid().ToString('N')).tmp"
try {
    $credential | Export-Clixml -LiteralPath $temporary
    if (Test-Path -LiteralPath $path) {
        if ($mode -ne 'refresh') { throw 'A credential file appeared while registering. Run setup again to verify it.' }
        [IO.File]::Replace($temporary, $path, $null)
    } else {
        [IO.File]::Move($temporary, $path)
    }
} finally {
    [IO.File]::Delete($temporary)
}
