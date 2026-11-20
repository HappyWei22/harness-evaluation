function Get-HarnessGuestCredentialPath {
    param([ValidateSet('Harness-DSH-Win11','Harness-WorkBuddy-Win11')][string]$VmName = 'Harness-DSH-Win11')
    if ($env:OS -ne 'Windows_NT' -or -not $env:LOCALAPPDATA) {
        throw 'Harness guest credentials require a Windows host user profile.'
    }
    Join-Path $env:LOCALAPPDATA "Harness-Eval\Credentials\$VmName.xml"
}

function Get-HarnessGuestCredential {
    param(
        [switch]$RequireSaved,
        [ValidateSet('Harness-DSH-Win11','Harness-WorkBuddy-Win11')]
        [string]$VmName = 'Harness-DSH-Win11'
    )

    $path = Get-HarnessGuestCredentialPath -VmName $VmName
    if (Test-Path -LiteralPath $path) {
        try {
            $credential = Import-Clixml -LiteralPath $path -ErrorAction Stop
            if ($credential -isnot [pscredential] -or -not $credential.UserName -or -not $credential.Password.Length) {
                throw 'Invalid cached credential object.'
            }
        } catch {
            throw 'The saved VM credential could not be loaded. Refresh it on this Windows host before continuing.'
        }
        Write-Host 'Using the saved Windows credential for the virtual machine.'
        return $credential
    }
    if ($RequireSaved) { throw 'No saved VM credential was found for this Windows host user.' }
    Write-Host 'Enter the Windows credential for the virtual machine.'
    $credential = Get-Credential
    if ($null -eq $credential) { throw 'Guest credential entry was cancelled.' }
    return $credential
}
