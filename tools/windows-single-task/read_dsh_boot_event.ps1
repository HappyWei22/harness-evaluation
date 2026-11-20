$ErrorActionPreference = 'Stop'

if ($env:USERPROFILE -ne 'C:\Users\alex_') {
    throw 'The Windows test profile differs from the registered profile.'
}

$event = Get-WinEvent -FilterHashtable @{
    LogName = 'System'
    ProviderName = 'Microsoft-Windows-Kernel-General'
    Id = 12
} -MaxEvents 1
if ($null -eq $event -or $event.RecordId -le 0 -or $null -eq $event.TimeCreated) {
    throw 'The latest Windows startup event could not be identified.'
}
[pscustomobject]@{
    RecordId = [long]$event.RecordId
    CreatedUtc = $event.TimeCreated.ToUniversalTime().ToString('o')
}
