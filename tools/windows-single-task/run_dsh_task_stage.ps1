param(
    [Parameter(Mandatory = $true)][string]$StageDirectory,
    [Parameter(Mandatory = $true)][string]$PackageDirectory
)

# Elevated host stage for one task run. It only drives the virtual machine; it never writes
# the attempt ledger, the permit, or the closing receipts. It stops after the prompt has been
# pasted and read back, and sends only after the unelevated entry writes go.json, so the
# durable "submitting" marker always exists before the interface is allowed to send.

$ErrorActionPreference = 'Stop'
$requestPath = Join-Path $StageDirectory 'request.json'
$reportPath = Join-Path $StageDirectory 'report.json'
$readyPath = Join-Path $StageDirectory 'ready.json'
$goPath = Join-Path $StageDirectory 'go.json'
$isolationPath = Join-Path $StageDirectory 'isolation-preflight.json'
$request = Get-Content -LiteralPath $requestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$guestRoot = $request.WorkingRoot
$guestScene = Join-Path $guestRoot 'probe\probe'
$isolationSource = [IO.File]::ReadAllText((Join-Path $PackageDirectory 'task_isolation_environment.ps1'))
. ([scriptblock]::Create($isolationSource))

$report = [ordered]@{
    RequestId   = $request.RequestId
    Activity    = $request.Activity
    CaseVersion = $request.CaseVersion
    ProductVersion = $request.ProductVersion
    Model       = 'not applicable'
    Skills      = 'not applicable'
    Tools       = 'Windows Task Scheduler, UI Automation, PowerShell Direct'
    Permissions = 'elevated host process, interactive guest user session'
    StartedAt   = (Get-Date).ToString('o')
    FinishedAt  = $null
    EvidenceLevel = 'E2'
    BaselineRestored = $null
    Verdict     = 'incomplete'
    Stage       = 'start'
    Error       = $null
    BootEvent   = $null
    Session     = $null
    Workspace   = $null
    Launch      = $null
    Dispatch    = $null
    Completion  = $null
    NativeAcceptance = $null
    NativeEvidenceError = $null
    ScenePreserved = $false
    Outputs     = @()
    SubmissionPermitted = $false
    FormalSubmissions = 0
    DiagnosticSubmissions = 0
    Isolation = $null
    ManualSteps = @()
    Cleanup     = $null
    FinalRestore = $null
}

$script:workbenchSequence = 0
$script:workbenchLast = $null
$script:workbenchGeneration = [Guid]::NewGuid().ToString('N')
function Save-StageReport {
    $temporary = "$reportPath.tmp"
    [IO.File]::WriteAllText($temporary, ($report | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $reportPath) { [IO.File]::Delete($reportPath) }
    [IO.File]::Move($temporary, $reportPath)
    if ($request.KeepProgress) {
        # Keep first observations even while the webpage and its host process are closed.
        # Exclude repeated polling writes that did not change an observed result.
        $signature = @($report.Stage; $report.Dispatch.InvokeStartedAt; $report.Dispatch.InvokeReturnedAt;
            $report.NativeAcceptance.AcceptanceVerified; $report.TurnObservedAt; $report.ObservationLimitAt;
            $report.CancelRequestedAt; $report.Outputs.Count; $report.ArchivedAt; $report.PoweredOffAt;
            $report.FinalRestore.Verdict; $report.CleanupError; $report.Error; $report.FinishedAt) -join '|'
        if ($signature -cne $script:workbenchLast) {
            try {
                $directory = Join-Path $StageDirectory "progress\$script:workbenchGeneration"
                $null = [IO.Directory]::CreateDirectory($directory)
                $script:workbenchSequence++
                $path = Join-Path $directory ('{0:D6}.json' -f $script:workbenchSequence)
                $payload = [ordered]@{ ObservedAt = (Get-Date).ToString('o'); Report = $report }
                [IO.File]::WriteAllText("$path.tmp", ($payload | ConvertTo-Json -Depth 14),
                    [Text.UTF8Encoding]::new($false))
                [IO.File]::Move("$path.tmp", $path)
                $script:workbenchLast = $signature
            } catch {
                Write-Warning "Could not retain workbench progress: $($_.Exception.Message)"
            }
        }
    }
}

function Wait-StageJson([string]$path) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $abort = Join-Path (Split-Path -Parent $path) 'abort.json'
    while (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        if (Test-Path -LiteralPath $abort -PathType Leaf) { throw 'Host stopped this stage before release.' }
        if ($timer.Elapsed.TotalSeconds -ge 300) { throw "The host did not release $([IO.Path]::GetFileName($path))." }
        Start-Sleep -Milliseconds 500
    }
    Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Invoke-RestoreStage([string]$phase, $cleanupContext) {
    $restoreReport = Join-Path (Join-Path $StageDirectory 'restore') "$phase.json"
    $source = [IO.File]::ReadAllText((Join-Path $PackageDirectory 'restore_dsh_baseline.ps1'))
    $arguments = @{
        VmId           = $request.VmId
        CheckpointId   = $request.CheckpointId
        CheckpointName = $request.CheckpointName
        ReportPath     = $restoreReport
        BootScriptPath = (Join-Path $PackageDirectory 'read_dsh_boot_event.ps1')
        ExpectedProfile = $request.ExpectedProfile
    }
    if ($cleanupContext) { $arguments.CleanupContextJson = ($cleanupContext | ConvertTo-Json -Depth 6 -Compress) }
    & ([scriptblock]::Create($source)) @arguments
}

function Connect-Guest {
    $credential = Get-HarnessGuestCredential -RequireSaved
    $wait = [System.Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        try { return New-PSSession -VMId ([guid]$request.VmId) -Credential $credential -ErrorAction Stop }
        catch {
            if ($wait.Elapsed.TotalSeconds -ge 120) { throw 'PowerShell Direct could not connect to the restored guest.' }
            Start-Sleep -Seconds 2
        }
    }
}

function Wait-Desktop($session, [int]$seconds) {
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    do {
        $facts = Invoke-Command -Session $session -ScriptBlock {
            # PowerShell Direct lands in session 0, so its own session id says nothing about
            # whether a person is signed in. Any Explorer outside session 0 is the real signal.
            $desktop = @(Get-Process explorer -ErrorAction SilentlyContinue |
                Where-Object SessionId -ne 0 | Select-Object Id, SessionId)
            [pscustomobject]@{
                SessionId        = [System.Diagnostics.Process]::GetCurrentProcess().SessionId
                UserProfile      = $env:USERPROFILE
                ExplorerCount    = $desktop.Count
                ExplorerSessions = @($desktop | ForEach-Object { $_.SessionId })
                LogonUi          = @(Get-Process LogonUI -ErrorAction SilentlyContinue |
                    Select-Object -ExpandProperty SessionId)
                DshCount         = @(Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue).Count
            }
        }
        if ($facts.ExplorerCount -ge 1) { return $facts }
        Start-Sleep -Seconds 3
    } while ($timer.Elapsed.TotalSeconds -lt $seconds)
    $facts
}

function Stop-GuestDsh($session) {
    Invoke-Command -Session $session -ScriptBlock {
        $ErrorActionPreference = 'Continue'
        foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name = 'DeepSeek Harness.exe'" -ErrorAction SilentlyContinue)) {
            try { & taskkill.exe /PID $process.ProcessId /T /F *> $null } catch { }
        }
        Start-Sleep -Milliseconds 800
        @(Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue).Count
    }
}

function Wait-VmOff([int]$seconds) {
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt $seconds) {
        if ((Get-VM -Id ([guid]$request.VmId)).State -eq 'Off') { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Read-NativeSnapshot($session, [string]$workspace) {
    Invoke-Command -Session $session -ArgumentList $workspace -ScriptBlock {
        param($path)
        $homePath = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
        $sessions = Join-Path $homePath 'sessions'
        $project = Join-Path $sessions ('--' + ($path -replace '[:\\/]+', '-') + '--')
        if (-not (Test-Path -LiteralPath $project -PathType Container)) { return }
        foreach ($directory in @($homePath, $sessions, $project)) {
            if ((Get-Item -LiteralPath $directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'The DSH session path contains a reparse point.'
            }
        }
        $entries = @(Get-ChildItem -LiteralPath $project -Directory -Force | Where-Object Name -match '^session-[a-f0-9-]+$')
        if (-not $entries.Count) { return }
        if ($entries.Count -ne 1 -or ($entries[0].Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'The work area does not have a unique regular DSH session directory.'
        }
        foreach ($name in 'session.v4.jsonl.zstd', 'session.v4.jsonl') {
            $trace = Join-Path $entries[0].FullName $name
            if (-not (Test-Path -LiteralPath $trace -PathType Leaf)) { continue }
            $item = Get-Item -LiteralPath $trace -Force
            if ($item.Length -gt 8MB -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw 'The DSH native trace is oversized or is a reparse point.'
            }
            try { $bytes = [IO.File]::ReadAllBytes($trace) } catch { return }
            if ($bytes.Length -gt 8MB) { throw 'The DSH native trace exceeded 8 MiB.' }
            [pscustomobject]@{ Name = $name; SessionId = $entries[0].Name; Encoded = [Convert]::ToBase64String($bytes) }
            return
        }
    }
}

$guestSession = $null
$taskName = "Harness-Task-$($request.RequestId)"
$failure = $null
$archiveReady = $false
$ownsRunScene = $false
$contextHash = $null
$isolationProof = $null
$isolationSha256 = $null
$isolationSpec = $null
try {
    # ------------------------------------------------------------ 1. registered baseline
    $report.Stage = 'validate_baseline'
    Save-StageReport
    $decoderSupport = & node -p "typeof require('node:zlib').zstdDecompressSync"
    if ($LASTEXITCODE -ne 0 -or $decoderSupport -ne 'function') {
        throw 'The host Node.js runtime cannot read DSH compressed native traces.'
    }
    $vm = Get-VM -Id ([guid]$request.VmId) -ErrorAction Stop
    if ($request.Attach) {
        # An attached run keeps the machine exactly as the operator left it: no shutdown and no
        # restore. The price is that the session did not start from the registered baseline, so
        # the evidence level says so and the attempt is recorded as diagnostic.
        if ($vm.State -ne 'Running') {
            throw "An attached run needs a running guest; the machine is $($vm.State)."
        }
        $report.BaselineRestored = $false
        $report.EvidenceLevel = 'E2-attached'
        $report.BootEvent = [pscustomobject]@{ RecordId = $null; CreatedUtc = $null; Source = 'attached_session' }
        $report.ManualSteps += 'Attached to a desktop started by hand; no baseline restore was performed.'
        Save-StageReport
    } else {
    $checkpoint = Get-VMSnapshot -VMName $vm.Name | Where-Object Id -eq ([guid]$request.CheckpointId)
    if (-not $checkpoint -or $checkpoint.Name -ne $request.CheckpointName) {
        throw "The registered checkpoint $($request.CheckpointName) is missing or renamed."
    }
    if ($vm.State -ne 'Off') {
        # Only a clean scene may be shut down automatically: no assistant process and no
        # leftover task from this request. Anything else keeps the scene for an operator.
        $guestSession = Connect-Guest
        $before = Invoke-Command -Session $guestSession -ScriptBlock {
            [pscustomobject]@{
                Dsh = @(Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue).Count
                Task = @(Get-ScheduledTask -TaskName 'Harness-Task-*' -ErrorAction SilentlyContinue).Count
            }
        }
        if ($before.Dsh -ne 0 -or $before.Task -ne 0) {
            throw "The virtual machine is running with unknown activity (dsh=$($before.Dsh), task=$($before.Task)); the scene is kept."
        }
        $report.Stage = 'shutdown_before_restore'
        Save-StageReport
        Invoke-Command -Session $guestSession -ScriptBlock { shutdown.exe /s /t 0 | Out-Null }
        Remove-PSSession $guestSession
        $guestSession = $null
        if (-not (Wait-VmOff 120)) { throw 'The virtual machine did not reach the Off state.' }
    }

    $report.Stage = 'restore_baseline'
    Save-StageReport
    $first = Invoke-RestoreStage 'first' $null
    if ($first.Verdict -ne 'pass') { throw "The baseline restore failed: $($first.Error)" }
    $report.BootEvent = $first.Guest.BootEvent
    $report.BootTime = $first.Guest.BootTime
    $report.BaselineRestored = $true
    Save-StageReport
    }

    # ------------------------------------------------------------ 2. interactive desktop
    $report.Stage = 'desktop_session'
    Save-StageReport
    $guestSession = Connect-Guest
    $facts = Wait-Desktop $guestSession 90
    if ($facts.ExplorerCount -lt 1 -and [int]$request.WaitForSignInSeconds -gt 0) {
        # The baseline has no automatic logon, so this is the one step that needs a person.
        # Waiting here keeps the restored machine in place instead of failing and restoring again.
        $report.Stage = 'await_operator_signin'
        Save-StageReport
        $signInTimer = [System.Diagnostics.Stopwatch]::StartNew()
        while ($signInTimer.Elapsed.TotalSeconds -lt [int]$request.WaitForSignInSeconds) {
            Start-Sleep -Seconds 5
            $facts = Wait-Desktop $guestSession 10
            if ($facts.ExplorerCount -ge 1) { break }
        }
        if ($facts.ExplorerCount -ge 1) {
            $report.ManualSteps += ('Operator signed in to the guest desktop after {0} s.' -f [int]$signInTimer.Elapsed.TotalSeconds)
        }
    }
    $report.Session = $facts
    if ($facts.ExplorerCount -lt 1) {
        $report.ManualSteps += 'Sign in to the guest desktop: no interactive desktop is available.'
        throw 'The guest has no signed-in interactive desktop; the run needs an operator sign-in.'
    }
    if ($facts.UserProfile -ne $request.ExpectedProfile) {
        throw "The signed-in profile is $($facts.UserProfile), not $($request.ExpectedProfile)."
    }

    if (-not $request.Attach) {
        $report.Stage = 'isolation_preflight'
        $isolationProof = [ordered]@{
            ProbeId = $request.IsolationProbeId; RequestId = $request.RequestId
            Activity = 'interface_preflight'; CaseVersion = 'Windows grading-file isolation v2'
            ProductVersion = 'not applicable'; Model = 'not applicable'; Skills = 'not applicable'
            Tools = 'PowerShell Direct, Windows Task Scheduler, Windows file open and SMB diagnostics'
            Permissions = 'elevated host process and the DSH interactive Windows account'
            StartedAt = (Get-Date).ToString('o'); FinishedAt = $null; EvidenceLevel = 'E2'
            Verdict = 'incomplete'; Error = $null; Context = $null; ContextSha256 = $null; Guest = $null
            DesktopSessions = @($facts.ExplorerSessions); DshSubmissions = 0
        }
        $report.Isolation = $isolationProof
        Save-StageReport
        if ($facts.DshCount -ne 0 -or -not $request.IsolationProbeId) {
            throw 'Formal startup requires an idle desktop and a registered Windows isolation preflight.'
        }
        $context = Get-TaskIsolationContext $guestSession $request
        $isolationProof.Context = $context
        Assert-TaskIsolationContext $context $request
        if ($context.BootEvent.RecordId -ne $report.BootEvent.RecordId -or
            $context.BootEvent.CreatedUtc -cne $report.BootEvent.CreatedUtc) {
            throw 'The guest rebooted after the registered baseline restore.'
        }
        $contextHash = Get-IsolationContextHash $context
        $isolationProof.ContextSha256 = $contextHash
        $isolationSpec = [ordered]@{
            ProbeId = $request.IsolationProbeId; Profile = $context.GuestProfile; Sid = $context.GuestSid
            Paths = @($context.ProtectedFiles | ForEach-Object { $_.UNC }); BootEvent = $context.BootEvent
            DesktopSessions = @($facts.ExplorerSessions); BlockedHResults = @($request.BlockedHResults)
        }
    }

    # ------------------------------------------------------------ 3. prepare workspace
    $report.Stage = 'prepare_workspace'
    Save-StageReport
    $workspace = Join-Path $guestRoot ('WS-' + $request.RequestId.Substring([math]::Max(0, $request.RequestId.Length - 8)))
    Invoke-Command -Session $guestSession -ArgumentList $guestRoot -ScriptBlock {
        param($root)
        if (Test-Path -LiteralPath $root) { throw 'The request workspace root already exists.' }
        New-Item -ItemType Directory -Path $root | Out-Null
    }
    $ownsRunScene = $true
    Invoke-Command -Session $guestSession -ArgumentList $guestRoot -ScriptBlock {
        param($root) New-Item -ItemType Directory -Path (Join-Path $root 'probe') | Out-Null
    }
    Copy-Item -LiteralPath (Join-Path $PackageDirectory 'guest_desktop_probe.ps1') -Destination (Join-Path $guestRoot 'probe\guest_desktop_probe.ps1') -ToSession $guestSession -Force
    if ($isolationSpec) {
        Copy-Item -LiteralPath (Join-Path $PackageDirectory 'task_isolation_probe.ps1') -Destination (Join-Path $guestRoot 'probe\task_isolation_probe.ps1') -ToSession $guestSession
    }
    Copy-Item -LiteralPath (Join-Path $StageDirectory 'prompt.txt') -Destination (Join-Path $guestRoot 'probe\prompt.txt') -ToSession $guestSession -Force
    Copy-Item -LiteralPath (Join-Path $StageDirectory 'inputs') -Destination (Join-Path $guestRoot 'probe\inputs') -ToSession $guestSession -Recurse -Force
    $prepared = Invoke-Command -Session $guestSession -ArgumentList $request, $workspace, $guestRoot, $isolationSpec -ScriptBlock {
        param($record, $ws, $root, $isolation)
        $ErrorActionPreference = 'Stop'
        $desktop = [Environment]::GetFolderPath('DesktopDirectory')
        $shortcut = Join-Path $desktop $record.ShortcutName
        if (-not (Test-Path -LiteralPath $shortcut -PathType Leaf)) { throw 'The DSH accessibility shortcut is missing.' }
        $link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
        foreach ($name in @('input', 'output')) {
            New-Item -ItemType Directory -Path (Join-Path $ws $name) -Force | Out-Null
        }
        $copied = @()
        foreach ($item in $record.Inputs) {
            $source = Join-Path (Join-Path $root 'probe\inputs') $item.path
            $target = Join-Path $ws $item.path
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "The staged input is missing: $($item.path)" }
            Copy-Item -LiteralPath $source -Destination $target -Force
            $hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
            $copied += [pscustomobject]@{ path = $item.path; sha256 = $hash; matches = ($hash -eq $item.sha256) }
        }
        $probeRequest = [ordered]@{
            RunId          = $record.RequestId
            Workspace      = $ws
            WorkspaceLabel = Split-Path -Leaf $ws
            Shortcut       = $shortcut
            Executable     = $link.TargetPath
            AccessibilityArgument = $record.AccessibilityArgument
            PromptFile     = (Join-Path $root 'probe\prompt.txt')
            ProbeHome      = (Join-Path $root 'probe')
            GoTimeoutSeconds = 300
            IsolationProbe = $isolation
        }
        [IO.File]::WriteAllText((Join-Path $root 'probe\task-request.json'),
            ($probeRequest | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
        [pscustomobject]@{ Workspace = $ws; Inputs = $copied; Shortcut = $shortcut; Executable = $link.TargetPath
            NativeNotBeforeMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() }
    }

    # Inputs must arrive byte-identical before anything is typed into the assistant.
    $report.Workspace = $prepared
    Save-StageReport
    if (@($prepared.Inputs | Where-Object { -not $_.matches }).Count -gt 0) {
        throw 'A copied input does not match its frozen fingerprint.'
    }

    # ------------------------------------------------------------ 4. interactive task
    $report.Stage = 'launch_task'
    Save-StageReport
    $launched = Invoke-Command -Session $guestSession -ArgumentList $taskName, $guestRoot, ([bool]$contextHash) -ScriptBlock {
        param($name, $root, $strict)
        $ErrorActionPreference = 'Stop'
        # A leftover task from an interrupted run would drive the same desktop at the same time,
        # and a running assistant would be reused instead of started fresh, so both are cleared.
        $pendingTasks = @(Get-ScheduledTask -TaskName 'Harness-Task-*' -ErrorAction SilentlyContinue)
        $dshProcesses = @(Get-CimInstance Win32_Process -Filter "Name = 'DeepSeek Harness.exe'" -ErrorAction SilentlyContinue)
        if ($strict -and ($pendingTasks.Count -or $dshProcesses.Count)) {
            throw 'The registered desktop already has a scheduled probe or a running DSH process.'
        }
        $pendingTasks | Unregister-ScheduledTask -Confirm:$false
        foreach ($process in $dshProcesses) {
            try { & taskkill.exe /PID $process.ProcessId /T /F *> $null } catch { }
        }
        Start-Sleep -Milliseconds 1200
        $probe = Join-Path $root 'probe'
        $script = Join-Path $probe 'guest_desktop_probe.ps1'
        $taskRequest = Join-Path $probe 'task-request.json'
        # The guest blocks .ps1 files by execution policy, so the task loads the file text.
        $argument = "-NoLogo -NoProfile -ExecutionPolicy Bypass -MTA -Command `"& { `$source = [IO.File]::ReadAllText('$script'); & ([scriptblock]::Create(`$source)) -RequestPath '$taskRequest' -ProbeHome '$probe' -TaskMode }`""
        $action = New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument $argument
        $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
        Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Force | Out-Null
        Start-ScheduledTask -TaskName $name
        Start-Sleep -Seconds 3
        $task = Get-ScheduledTask -TaskName $name
        [pscustomobject]@{ State = [string]$task.State; Argument = $argument }
    }
    $report.Launch = $launched

    # ------------------------------------------------------- 5. wait for the pasted prompt
    $report.Stage = 'await_prompt'
    Save-StageReport
    $guestReady = Join-Path $guestScene 'ready.json'
    $guestIsolation = Join-Path $guestScene 'isolation-read.json'
    $failureNote = Join-Path $guestRoot 'probe\probe-failure.txt'
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $ready = $null
    $sawRunning = $false
    while ($timer.Elapsed.TotalSeconds -lt $request.PromptTimeoutSeconds) {
        # The task lives in the guest; asking the host for it always returns nothing and would
        # look identical to a task that died, so every probe below runs inside the guest.
        $probe = Invoke-Command -Session $guestSession -ArgumentList $guestReady, $taskName, $failureNote, $guestIsolation -ScriptBlock {
            param($readyPath, $name, $notePath, $isolationPath)
            $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            $info = if ($task) { Get-ScheduledTaskInfo -TaskName $name -ErrorAction SilentlyContinue } else { $null }
            [pscustomobject]@{
                Ready       = Test-Path -LiteralPath $readyPath -PathType Leaf
                Isolation   = if (Test-Path -LiteralPath $isolationPath -PathType Leaf) {
                                  Get-Content -LiteralPath $isolationPath -Raw -Encoding UTF8 | ConvertFrom-Json
                              } else { $null }
                TaskExists  = [bool]$task
                TaskState   = if ($task) { [string]$task.State } else { $null }
                LastResult  = if ($info) { $info.LastTaskResult } else { $null }
                LastRunTime = if ($info) { $info.LastRunTime.ToString('o') } else { $null }
                Note        = if (Test-Path -LiteralPath $notePath -PathType Leaf) {
                                  [IO.File]::ReadAllText($notePath)
                              } else { $null }
            }
        }
        if ($isolationProof -and $probe.Isolation -and -not $isolationSha256) {
            $isolationProof.Guest = $probe.Isolation.Facts
            $isolationProof.Verdict = $probe.Isolation.Verdict
            $isolationProof.Error = $probe.Isolation.Error
            $isolationProof.FinishedAt = (Get-Date).ToString('o')
            [IO.File]::WriteAllText($isolationPath, ($isolationProof | ConvertTo-Json -Depth 9), [Text.UTF8Encoding]::new($false))
            if ($isolationProof.Verdict -cne 'pass') { throw $isolationProof.Error }
            $isolationSha256 = (Get-FileHash -LiteralPath $isolationPath -Algorithm SHA256).Hash.ToLowerInvariant()
            Save-StageReport
        }
        if ($probe.Ready) {
            $ready = Invoke-Command -Session $guestSession -ArgumentList $guestReady -ScriptBlock {
                param($path) Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
            }
            break
        }
        if ($probe.TaskState -eq 'Running') { $sawRunning = $true }
        if (-not $probe.TaskExists) {
            throw "The guest task disappeared before pasting the prompt (note=$($probe.Note))."
        }
        # 0x41301 is still starting, 0x41303 never ran; any other code on a Ready task is a failure.
        $finished = $probe.TaskState -eq 'Ready' -and $probe.LastRunTime
        $placeholder = @(267009, 267011, 267014) -contains [int]$probe.LastResult
        if ($finished -and -not $placeholder -and [int]$probe.LastResult -ne 0) {
            throw "The guest task stopped with result $($probe.LastResult) before pasting the prompt (note=$($probe.Note))."
        }
        if ($finished -and [int]$probe.LastResult -eq 0) {
            throw "The guest task ended without writing a prompt report (note=$($probe.Note))."
        }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { throw 'The guest did not report a pasted prompt inside the allowed time.' }
    if ($ready.Workspace -ine $workspace -or $ready.PromptReadbackSha256 -cne $request.PromptSha256) {
        throw 'The reported work area or readback does not match the staged request.'
    }
    if ($contextHash) {
        if (-not $isolationSha256) { throw 'The interactive Windows isolation preflight is missing.' }
        $current = Get-TaskIsolationContext $guestSession $request
        if ((Get-IsolationContextHash $current) -cne $contextHash) {
            throw 'The boot or access configuration changed before the permit could be issued.'
        }
        Invoke-Command -Session $guestSession -ArgumentList $workspace, $request -ScriptBlock {
            param($path, $record)
            $outputDir = Join-Path $path 'output'
            if (@(Get-ChildItem -LiteralPath $outputDir -Force).Count) { throw 'The staged output directory is not empty.' }
            $inputDir = Join-Path $path 'input'
            $files = @(Get-ChildItem -LiteralPath $inputDir -File -Recurse -Force)
            if ($files.Count -ne @($record.Inputs).Count) { throw 'The staged input file list changed.' }
            foreach ($entry in $record.Inputs) {
                $hash = (Get-FileHash -LiteralPath (Join-Path $path $entry.path) -Algorithm SHA256).Hash
                if ($hash -ne $entry.sha256) { throw 'A staged input changed before send admission.' }
            }
        }
        $ready | Add-Member -NotePropertyName IsolationContextSha256 -NotePropertyValue $contextHash
        $ready | Add-Member -NotePropertyName IsolationProofSha256 -NotePropertyValue $isolationSha256
    }
    $ready | Add-Member -NotePropertyName BootEvent -NotePropertyValue $report.BootEvent -Force
    [IO.File]::WriteAllText($readyPath, ($ready | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
    $report.Workspace = [pscustomobject]@{
        Path                     = $ready.Workspace
        PromptMatches            = $ready.PromptMatches
        Readback                 = $ready.PromptReadbackSha256
        ReadbackLength           = $ready.ReadbackLength
        ReadbackPreview          = $ready.ReadbackPreview
        ComposerLeftoverLength   = $ready.ComposerLeftoverLength
        ComposerCleared          = $ready.ComposerCleared
        ComposerClearRoute       = $ready.ComposerClearRoute
        ComposerLengthAfterClear = $ready.ComposerLengthAfterClear
    }

    $report.Configuration = $ready.Configuration
    $report.PreparedInputs = $prepared.Inputs

    # ------------------------------------------------------- 6. wait for the send authorisation
    $report.Stage = 'await_authorisation'
    Save-StageReport
    $authorisation = Wait-StageJson $goPath
    if ($contextHash) {
        $current = Get-TaskIsolationContext $guestSession $request
        if ((Get-IsolationContextHash $current) -cne $contextHash -or
            $authorisation.isolation_context_sha256 -cne $contextHash -or
            $authorisation.isolation_proof_sha256 -cne $isolationSha256) {
            throw 'The isolation binding changed before the staged send could be released.'
        }
    }
    $goGuest = Join-Path $guestScene 'go.json'
    if ($request.Attach) { $report.DiagnosticSubmissions = $null } else { $report.FormalSubmissions = $null }
    Copy-Item -LiteralPath $goPath -Destination "$goGuest.tmp" -ToSession $guestSession -Force
    Invoke-Command -Session $guestSession -ArgumentList $goGuest -ScriptBlock {
        param($path)
        [IO.File]::Move("$path.tmp", $path)
    }
    $report.SubmissionPermitted = $true

    # Observe native history even if the UI call returned late or threw after reaching DSH.
    $report.Stage = 'await_native_turn'
    Save-StageReport
    $guestReport = Join-Path $guestScene 'report.json'
    $nativeInspector = Join-Path $PackageDirectory 'inspect_task_native.py'
    $promptFile = Join-Path $StageDirectory 'prompt.txt'
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $lastForensicAt = -60.0
    while ($timer.Elapsed.TotalSeconds -lt $request.TaskTimeoutSeconds) {
        $candidate = Invoke-Command -Session $guestSession -ArgumentList $guestReport -ScriptBlock {
            param($path)
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                Get-Content -LiteralPath $path -Raw -Encoding UTF8 -ErrorAction SilentlyContinue | ConvertFrom-Json
            }
        }
        if ($candidate.Dispatch) { $report.Dispatch = $candidate.Dispatch }
        if ($candidate.FinishedAt -and -not $candidate.Dispatch.InvokeStartedAt) {
            if ($request.Attach) { $report.DiagnosticSubmissions = 0 } else { $report.FormalSubmissions = 0 }
            throw "The guest stopped before invoking the send control: $($candidate.Error)"
        }
        if ($candidate.Dispatch.InvokeStartedAt) {
            $sample = Read-NativeSnapshot $guestSession $workspace
            if ($sample) {
                $snapshotPath = Join-Path $StageDirectory $sample.Name
                [IO.File]::WriteAllBytes($snapshotPath, [Convert]::FromBase64String($sample.Encoded))
                $result = & $request.PythonExecutable -B $nativeInspector $snapshotPath $promptFile $workspace $sample.SessionId ($prepared.NativeNotBeforeMs.ToString())
                if ($LASTEXITCODE -ne 0) { throw 'The host native trace inspector failed.' }
                $native = $result | ConvertFrom-Json
                $report.NativeEvidenceError = $native.EvidenceError
                $report.NativeAcceptance = $native.Acceptance
                if ($native.Acceptance) {
                    if ($request.Attach) { $report.DiagnosticSubmissions = 1 } else { $report.FormalSubmissions = 1 }
                } else {
                    if ($request.Attach) { $report.DiagnosticSubmissions = $null } else { $report.FormalSubmissions = $null }
                }
                if ($native.NativeTurnEnd) {
                    $native | Add-Member -NotePropertyName TraceName -NotePropertyValue $sample.Name
                    $native | Add-Member -NotePropertyName TraceSha256 -NotePropertyValue (Get-FileHash -LiteralPath $snapshotPath -Algorithm SHA256).Hash.ToLowerInvariant()
                    $report.Completion = $native
                    $report.TurnObservedAt = (Get-Date).ToString('o')
                    break
                }
            }
        }
        if ($timer.Elapsed.TotalSeconds - $lastForensicAt -ge 20) {
            $lastForensicAt = $timer.Elapsed.TotalSeconds
            Save-StageReport
            $available = Invoke-Command -Session $guestSession -ArgumentList $guestScene -ScriptBlock {
                param($directory)
                foreach ($name in 'report.json', 'after-prompt-paste.png', 'after-send.png') {
                    if (Test-Path -LiteralPath (Join-Path $directory $name) -PathType Leaf) { $name }
                }
            }
            foreach ($name in $available) {
                $source = Join-Path $guestScene $name
                $target = Join-Path $StageDirectory ('forensic-' + $name)
                Copy-Item -LiteralPath $source -Destination $target -FromSession $guestSession -Force -ErrorAction SilentlyContinue
            }
        }
        Start-Sleep -Seconds 3
    }
    if (-not $report.Completion) {
        throw 'Native acceptance or termination is unconfirmed inside the allowed time. The guest is preserved.'
    }
    [IO.File]::WriteAllText((Join-Path $StageDirectory 'guest-report.json'),
        ($candidate | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))

    # ------------------------------------------------------- 8. collect declared outputs
    $report.Stage = 'collect_outputs'
    Save-StageReport
    $resultsDirectory = Join-Path $StageDirectory 'results'
    New-Item -ItemType Directory -Path $resultsDirectory -Force | Out-Null
    $collected = @()
    foreach ($relative in $request.Outputs) {
        $guestFile = Join-Path $workspace $relative
        $info = Invoke-Command -Session $guestSession -ArgumentList $guestFile -ScriptBlock {
            param($path)
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                return [pscustomobject]@{ Exists = $false }
            }
            $item = Get-Item -LiteralPath $path -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                return [pscustomobject]@{ Exists = $true; Reparse = $true }
            }
            [pscustomobject]@{
                Exists = $true; Reparse = $false; Size = $item.Length
                Sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
        $target = Join-Path $resultsDirectory $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        if ($info.Exists -and -not $info.Reparse) {
            Copy-Item -LiteralPath $guestFile -Destination $target -FromSession $guestSession -Force
            $hostHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
            $collected += [pscustomobject]@{
                Path = $relative; Size = $info.Size; Sha256 = $info.Sha256
                HostMatches = ($hostHash -eq $info.Sha256)
            }
        } else {
            $collected += [pscustomobject]@{
                Path = $relative; Size = $null; Sha256 = $null
                HostMatches = $false; Missing = $true; Reparse = [bool]$info.Reparse
            }
        }
    }
    $replyPath = Join-Path $StageDirectory 'final-reply.txt'
    if ($report.Completion.ReplyText) {
        [IO.File]::WriteAllText($replyPath, $report.Completion.ReplyText, [Text.UTF8Encoding]::new($false))
    }
    $report.Outputs = $collected
    $screenshots = Join-Path $StageDirectory 'screenshots'
    New-Item -ItemType Directory -Path $screenshots -Force | Out-Null
    foreach ($name in @('after-prompt-paste', 'after-send', 'after-workspace-selection', 'after-focus')) {
        $guestPng = Join-Path $guestScene "$name.png"
        $exists = Invoke-Command -Session $guestSession -ArgumentList $guestPng -ScriptBlock {
            param($path) Test-Path -LiteralPath $path -PathType Leaf
        }
        if ($exists) {
            Copy-Item -LiteralPath $guestPng -Destination (Join-Path $screenshots "$name.png") -FromSession $guestSession -Force
        }
    }
    if (@($collected | Where-Object { $_.Reparse -or (-not $_.Missing -and -not $_.HostMatches) }).Count) {
        throw 'An output was unsafe or changed during collection. The guest is preserved.'
    }
    $report.ArchivedAt = (Get-Date).ToString('o')
    $archiveReady = $true
    $report.Verdict = 'pass'
    Save-StageReport
} catch {
    $failure = $_.Exception.Message
    $report.Error = $failure
    $report.Verdict = 'blocked'
    if ($isolationProof -and $isolationProof.Verdict -eq 'incomplete') {
        $isolationProof.Verdict = 'blocked'
        $isolationProof.Error = $failure
        $isolationProof.FinishedAt = (Get-Date).ToString('o')
        [IO.File]::WriteAllText($isolationPath, ($isolationProof | ConvertTo-Json -Depth 9), [Text.UTF8Encoding]::new($false))
    }
} finally {
    # Any incomplete run may have an existing or live guest scene. Power changes require an archive.
    if (-not $archiveReady) {
        $report.ScenePreserved = $true
        $report.Stage = 'scene_preserved'
        if ($guestSession) { Remove-PSSession -Session $guestSession -ErrorAction SilentlyContinue }
    } else {
        $report.Stage = 'cleanup'
        Save-StageReport
        if ($guestSession -and $ownsRunScene) {
            # The probe keeps its report, screenshots, and failure note inside the guest, and the
            # working root is deleted just below. Copying the scene back first is what makes a failed
            # run explainable after the fact.
            try {
                $sceneDirectory = Join-Path $StageDirectory 'guest-scene'
                New-Item -ItemType Directory -Path $sceneDirectory -Force | Out-Null
                Copy-Item -LiteralPath $guestRoot -Destination $sceneDirectory -Recurse -Force -FromSession $guestSession -ErrorAction Stop
                $report.ScenePath = (Join-Path $sceneDirectory (Split-Path -Leaf $guestRoot))
            } catch { $report.SceneError = $_.Exception.Message }
            try {
                $report.Cleanup = Invoke-Command -Session $guestSession -ArgumentList $taskName, $guestRoot -ScriptBlock {
                    param($name, $root)
                    $ErrorActionPreference = 'Stop'
                    $expectedRoot = Join-Path (Join-Path $env:LOCALAPPDATA 'Temp') $name.Substring('Harness-Task-'.Length)
                    if ([IO.Path]::GetFullPath($root) -ine $expectedRoot) { throw 'The cleanup root is outside this request.' }
                    Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
                    foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name = 'DeepSeek Harness.exe'" -ErrorAction SilentlyContinue)) {
                        try { & taskkill.exe /PID $process.ProcessId /T /F *> $null } catch { }
                    }
                    Start-Sleep -Milliseconds 800
                    if (Test-Path -LiteralPath $root) {
                        $parent = Get-Item -LiteralPath (Split-Path -Parent $root) -Force
                        $item = Get-Item -LiteralPath $root -Force
                        if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                            ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                            throw 'The staged cleanup path is not a regular request directory.'
                        }
                        Remove-Item -LiteralPath $root -Recurse -Force
                    }
                    [pscustomobject]@{
                        TaskRemains = @(Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue).Count
                        WorkingRootExists = Test-Path -LiteralPath $root
                        DshRemains = @(Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue).Count
                    }
                }
            } catch { $report.CleanupError = $_.Exception.Message }
        }

        if ($guestSession -and -not $request.Attach) {
            try {
                Invoke-Command -Session $guestSession -ScriptBlock { shutdown.exe /s /t 0 | Out-Null }
                Start-Sleep -Seconds 2
                Remove-PSSession -Session $guestSession -ErrorAction SilentlyContinue
                $guestSession = $null
                $null = Wait-VmOff 120
            } catch { $report.CleanupError = "shutdown: $($_.Exception.Message)" }
        } elseif ($guestSession) {
            # An attached run leaves the machine running and signed in: the operator asked to
            # continue from that desktop, so closing it would undo the point of attaching.
            Remove-PSSession -Session $guestSession -ErrorAction SilentlyContinue
            $guestSession = $null
        }

        $report.Stage = 'final_restore'
        Save-StageReport
        if ($request.Attach) {
            # Nothing is restored by design: the desktop the operator signed in to stays as it is.
            $report.FinalRestore = [pscustomobject]@{
                Verdict = 'skipped_by_attach'; BootEvent = $null; Residue = $null; Error = $null
            }
        } else {
        try {
            $context = [pscustomobject]@{
                AttemptId = if ($authorisation.attempt_id) { $authorisation.attempt_id } else { $request.AttemptPrefix }
                Workspace = $workspace
                SessionId = $report.Completion.Acceptance.SessionId
                OriginalBootRecordId = $report.BootEvent.RecordId
                OriginalBootCreatedUtc = $report.BootEvent.CreatedUtc
            }
            $final = Invoke-RestoreStage 'final' $context
            $report.FinalRestore = [pscustomobject]@{
                Verdict   = $final.Verdict
                BootEvent = $final.Guest.BootEvent
                Residue   = $final.Guest.RunResidue
                Error     = $final.Error
            }
        } catch {
            $report.FinalRestore = [pscustomobject]@{ Verdict = 'failed'; Error = $_.Exception.Message }
            if (-not $report.Error) { $report.Error = "final restore: $($_.Exception.Message)" }
        }
        }
    }

    $report.FinishedAt = (Get-Date).ToString('o')
    Save-StageReport
    if ($failure) { throw $failure }
}
