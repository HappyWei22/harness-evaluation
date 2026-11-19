# Inspect the registered SMB policy and the current host grading files.
# Capture current facts without changing the host firewall or the guest's connections.
function Get-TaskIsolationContext($session, $record) {
    $vm = Get-VM -Id ([guid]$record.VmId)
    $alias = 'vEthernet (Default Switch)'
    $adapter = @(Get-VMNetworkAdapter -VMName $vm.Name)
    $switch = Get-VMSwitch -Name 'Default Switch'
    $hostNic = Get-NetAdapter -Name $alias
    $ips = @(Get-NetIPAddress -InterfaceAlias $alias -AddressFamily IPv4 | Where-Object AddressState -eq 'Preferred')
    $profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore | Sort-Object Name)
    $rule = Get-NetFirewallRule -PolicyStore ActiveStore -Name 'Harness-Eval-G01-DefaultSwitch-SMB-Block'
    $ports = $rule | Get-NetFirewallPortFilter
    $bindings = @($rule | Get-NetFirewallInterfaceFilter | Select-Object -ExpandProperty InterfaceAlias)
    $addresses = $rule | Get-NetFirewallAddressFilter
    if ($vm.Name -ne $record.VmName -or $vm.State -ne 'Running' -or $adapter.Count -ne 1 -or
        $adapter[0].SwitchName -ne 'Default Switch' -or $switch.SwitchType -ne 'Internal' -or $ips.Count -ne 1 -or
        -not $profiles.Count -or @($profiles | Where-Object { [string]$_.Enabled -ne 'True' }).Count -or
        @($rule).Count -ne 1 -or $rule.Enabled -ne 'True' -or $rule.Action -ne 'Block' -or
        $rule.Direction -ne 'Inbound' -or $rule.Profile -ne 'Any' -or $bindings.Count -ne 1 -or
        $bindings[0] -notin @($alias, [string]$hostNic.InterfaceGuid, ([guid]$hostNic.InterfaceGuid).ToString('B')) -or
        [string]$ports.Protocol -notin @('6', 'TCP') -or (($ports.LocalPort | Sort-Object) -join ',') -ne '139,445' -or
        ($ports.RemotePort -join ',') -ne 'Any' -or ($addresses.LocalAddress -join ',') -ne 'Any' -or
        ($addresses.RemoteAddress -join ',') -ne 'Any') {
        throw 'The registered Default Switch and effective host SMB block policy were not verified.'
    }
    $share = Get-SmbShare -Name 'E$'
    if ($share.Path -ne 'E:\' -or -not $share.Special -or @($record.ProtectedFiles).Count -ne 2) {
        throw 'The registered E$ share or the grading-file list could not be verified.'
    }
    $hostIp = $ips[0].IPAddress
    $hostAddresses = @($hostIp) + @(Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
        Select-Object -ExpandProperty IPAddress)
    $hostAddresses = @($hostAddresses | Sort-Object -Unique)
    $protectedFiles = @(foreach ($entry in $record.ProtectedFiles) {
        $file = Get-Item -LiteralPath $entry.Path -Force
        if ($file.PSIsContainer -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            -not $file.FullName.StartsWith($share.Path, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'A grading file is outside the registered E$ share or is not a regular file.'
        }
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hash -cne $entry.Sha256) { throw 'A registered host grading file changed.' }
        [ordered]@{
            Role = $entry.Role; Path = $file.FullName; Sha256 = $hash
            UNC = ('\\{0}\E$\{1}' -f $hostIp, $file.FullName.Substring($share.Path.Length))
        }
    })

    $bootSource = [IO.File]::ReadAllText((Join-Path $PackageDirectory 'read_dsh_boot_event.ps1'))
    $guest = Invoke-Command -Session $session -ArgumentList $bootSource, $hostAddresses -ScriptBlock {
        param([string]$source, [string[]]$targets)
        $ErrorActionPreference = 'Stop'
        $boot = & ([scriptblock]::Create($source))
        $connections = [Collections.Generic.List[object]]::new()
        try {
            foreach ($target in $targets) {
                foreach ($port in 139, 445) {
                    $tcp = [Net.Sockets.TcpClient]::new()
                    $check = [pscustomobject]@{ Address = $target; Port = $port; Client = $tcp; Pending = $null }
                    $connections.Add($check)
                    $check.Pending = $tcp.BeginConnect($target, $port, $null, $null)
                }
            }
            $timer = [Diagnostics.Stopwatch]::StartNew()
            $portChecks = @(foreach ($check in $connections) {
                $remaining = [int][Math]::Max(0, 1500 - $timer.ElapsedMilliseconds)
                if ($check.Pending.AsyncWaitHandle.WaitOne($remaining)) {
                    try { $check.Client.EndConnect($check.Pending) } catch [Net.Sockets.SocketException] { }
                }
                [pscustomobject]@{ Address = $check.Address; Port = $check.Port; Reachable = $check.Client.Connected }
            })
        } finally {
            foreach ($check in $connections) {
                if ($check.Pending) { $check.Pending.AsyncWaitHandle.Dispose() }
                $check.Client.Dispose()
            }
        }
        [pscustomobject]@{
            RecordId = $boot.RecordId; CreatedUtc = $boot.CreatedUtc; Profile = $env:USERPROFILE
            Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
            SmbMappings = @(Get-SmbMapping).Count; SmbConnections = @(Get-SmbConnection).Count
            NetworkDisks = @(Get-CimInstance Win32_LogicalDisk | Where-Object { $_.DriveType -eq 4 -or $_.ProviderName }).Count
            Tcp = $portChecks
        }
    }
    [ordered]@{
        VmId = $vm.Id.ToString(); BootEvent = [ordered]@{ RecordId = $guest.RecordId; CreatedUtc = $guest.CreatedUtc }
        SwitchId = $switch.Id.ToString(); GuestAdapter = $adapter[0].MacAddress; HostAdapter = [string]$hostNic.InterfaceGuid
        HostIPv4 = $hostIp; Rule = $rule.Name; Interface = [string]$bindings[0]
        HostAddresses = $hostAddresses
        SmbEndpoints = @($guest.Tcp | Select-Object Address, Port, Reachable)
        GuestProfile = $guest.Profile; GuestSid = $guest.Sid
        SmbMappings = $guest.SmbMappings; SmbConnections = $guest.SmbConnections
        NetworkDisks = $guest.NetworkDisks; FirewallProfiles = @($profiles | ForEach-Object { "$($_.Name):$($_.DefaultInboundAction)" })
        ProtectedFiles = $protectedFiles
    }
}

function Assert-TaskIsolationContext($context, $record) {
    if ($context.GuestProfile -ne $record.ExpectedProfile -or -not $context.GuestSid) {
        throw 'The Windows access check did not use the registered guest account.'
    }
    if ($context.SmbMappings -ne 0 -or $context.SmbConnections -ne 0 -or $context.NetworkDisks -ne 0) {
        throw "The guest has Windows file-sharing state (mappings=$($context.SmbMappings), connections=$($context.SmbConnections), network disks=$($context.NetworkDisks))."
    }
    $reachable = @($context.SmbEndpoints | Where-Object Reachable)
    if ($reachable.Count) {
        $endpoints = ($reachable | ForEach-Object { '{0}:{1}' -f $_.Address, $_.Port }) -join ', '
        throw "Windows file-sharing ports are reachable from the guest: $endpoints."
    }
}

function Get-IsolationContextHash($context) {
    $digest = [Security.Cryptography.SHA256]::Create()
    try {
        $json = $context | ConvertTo-Json -Depth 6 -Compress
        [BitConverter]::ToString($digest.ComputeHash([Text.Encoding]::UTF8.GetBytes($json))).Replace('-', '').ToLowerInvariant()
    } finally { $digest.Dispose() }
}
