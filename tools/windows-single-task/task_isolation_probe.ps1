# Run in the same interactive scheduled task and under the same account as the DSH launcher.
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class HarnessDesktopProtocol {
    [DllImport("wtsapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool WTSQuerySessionInformation(IntPtr server, int session, int information,
        out IntPtr buffer, out int byteCount);

    [DllImport("wtsapi32.dll")]
    private static extern void WTSFreeMemory(IntPtr buffer);

    public static int Read(int session) {
        const int WTSClientProtocolType = 16;
        IntPtr buffer;
        int byteCount;
        if (!WTSQuerySessionInformation(IntPtr.Zero, session, WTSClientProtocolType, out buffer, out byteCount))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        try {
            if (buffer == IntPtr.Zero || byteCount < 2)
                throw new InvalidOperationException("The Windows session protocol was not reported.");
            return Marshal.ReadInt16(buffer);
        } finally {
            WTSFreeMemory(buffer);
        }
    }
}
'@

function Get-GuestIsolationFacts($spec) {
    $event = Get-WinEvent -FilterHashtable @{
        LogName = 'System'; ProviderName = 'Microsoft-Windows-Kernel-General'; Id = 12
    } -MaxEvents 1
    $checks = @(foreach ($path in $spec.Paths) {
        $result = [ordered]@{ Path = $path; CanOpen = $false; HResult = $null; Error = $null }
        try {
            $share = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
            $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, $share)
            try { $result.CanOpen = $true } finally { $stream.Dispose() }
        } catch {
            $errorDetail = $_.Exception.GetBaseException()
            $result.HResult = $errorDetail.HResult.ToString('X8')
            $result.Error = $errorDetail.Message
        }
        [pscustomobject]$result
    })
    $session = [Diagnostics.Process]::GetCurrentProcess().SessionId
    [pscustomobject]@{
        ProbeId = $spec.ProbeId; Profile = $env:USERPROFILE
        Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        SessionId = $session; SessionProtocol = [HarnessDesktopProtocol]::Read($session)
        BootEvent = [ordered]@{ RecordId = $event.RecordId; CreatedUtc = $event.TimeCreated.ToUniversalTime().ToString('o') }
        SmbMappings = @(Get-SmbMapping).Count; SmbConnections = @(Get-SmbConnection).Count
        NetworkDisks = @(Get-CimInstance Win32_LogicalDisk | Where-Object { $_.DriveType -eq 4 -or $_.ProviderName }).Count
        ReadChecks = $checks
    }
}

function Assert-GuestIsolationFacts($spec, $facts) {
    if ($facts.ProbeId -cne $spec.ProbeId -or $facts.Profile -ine $spec.Profile -or $facts.Sid -cne $spec.Sid -or
        $facts.SessionId -le 0 -or $facts.SessionId -notin $spec.DesktopSessions -or
        $facts.BootEvent.RecordId -ne $spec.BootEvent.RecordId -or $facts.BootEvent.CreatedUtc -cne $spec.BootEvent.CreatedUtc) {
        throw 'The isolation check ran outside the registered Windows desktop and boot.'
    }
    if ($facts.SessionProtocol -ne 0 -or $facts.SmbMappings -ne 0 -or $facts.SmbConnections -ne 0 -or $facts.NetworkDisks -ne 0) {
        throw 'The DSH desktop uses a remote protocol or has a Windows file-sharing mapping or connection.'
    }
    if (@($facts.ReadChecks | Where-Object CanOpen).Count) { throw 'Windows could open a host grading file for reading.' }
    if (@($facts.ReadChecks).Count -ne @($spec.Paths).Count -or
        @($facts.ReadChecks | Where-Object { $_.HResult -cnotin $spec.BlockedHResults }).Count) {
        throw 'Windows could not establish a definite grading-file access denial or network failure.'
    }
}
