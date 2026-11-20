param([string]$RequestPath, [string]$ProbeHome, [switch]$SelfTest, [switch]$TaskMode)

# Runs inside the guest's interactive session, started by a one-time scheduled task.
# It launches the DSH accessibility shortcut, records what the desktop exposes, and
# never types into the composer or invokes the send button.
# The guest blocks .ps1 files by execution policy, so the host invokes this file as a
# script block and passes the probe directory explicitly instead of relying on PSScriptRoot.

$ErrorActionPreference = 'Stop'
$probeHome = if ($ProbeHome) { $ProbeHome } else { $PSScriptRoot }

# Breadcrumbs written before any other work, so a start-up failure is still visible.
try {
    $stamp = 'pid={0} session={1} user={2} selfTest={3} at={4}' -f $PID,
        [System.Diagnostics.Process]::GetCurrentProcess().SessionId,
        "$env:USERDOMAIN\$env:USERNAME", [bool]$SelfTest, (Get-Date).ToString('o')
    [IO.File]::WriteAllText((Join-Path $probeHome 'probe-boot.txt'), $stamp, [Text.UTF8Encoding]::new($false))
} catch { }

trap {
    try {
        [IO.File]::WriteAllText((Join-Path $probeHome 'probe-trap.txt'), ($_ | Out-String), [Text.UTF8Encoding]::new($false))
    } catch { }
    break
}

if (-not $RequestPath) { $RequestPath = Join-Path $probeHome 'probe-request.json' }
if (-not (Test-Path -LiteralPath $RequestPath -PathType Leaf)) {
    [IO.File]::WriteAllText((Join-Path $probeHome 'probe-failure.txt'),
        "The probe request file is missing: $RequestPath", [Text.UTF8Encoding]::new($false))
    return
}
if ($SelfTest) {
    [IO.File]::WriteAllText((Join-Path $probeHome 'probe-selftest.txt'),
        ('request readable, workspace={0}' -f (Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json).Workspace),
        [Text.UTF8Encoding]::new($false))
    return
}
$request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$reportDirectory = Join-Path (Split-Path -Parent $RequestPath) 'probe'
New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
$reportPath = Join-Path $reportDirectory 'report.json'
$processName = 'DeepSeek Harness'

$report = [ordered]@{
    RunId              = $request.RunId
    Activity           = 'interface_preflight'
    CaseVersion        = 'desktop automation reachability v1'
    ProductVersion     = $request.ProductVersion
    Model              = 'not applicable'
    Skills             = 'not applicable'
    Tools              = 'Windows Task Scheduler, UI Automation, Windows Forms screen capture'
    Permissions        = 'interactive guest user session'
    StartedAt          = (Get-Date).ToString('o')
    FinishedAt         = $null
    EvidenceLevel      = 'E2'
    Verdict            = 'incomplete'
    Stage              = 'start'
    Error              = $null
    ExpectedWorkspace  = $request.WorkspaceLabel
    Session            = $null
    StateDiscovery     = $null
    WorkspacePicker    = $null
    WorkspaceSelection = $null
    Focus              = $null
    Task               = $null
    Dispatch           = $null
    Completion         = $null
    Steps              = @()
    Attempts           = @()
    Screenshots        = @()
}

function Save-Report {
    $temporary = "$reportPath.tmp"
    [IO.File]::WriteAllText($temporary, ($report | ConvertTo-Json -Depth 9), [Text.UTF8Encoding]::new($false))
    if ([IO.File]::Exists($reportPath)) {
        [IO.File]::Replace($temporary, $reportPath, [NullString]::Value)
    } else {
        [IO.File]::Move($temporary, $reportPath)
    }
}

function Add-Step([string]$name, [string]$outcome, $detail) {
    $report.Steps += [pscustomobject]@{ Name = $name; At = (Get-Date).ToString('o'); Outcome = $outcome; Detail = $detail }
    Save-Report
}

function Save-Screenshot([string]$name) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
        $bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
            $file = Join-Path $reportDirectory "$name.png"
            $bitmap.Save($file, [System.Drawing.Imaging.ImageFormat]::Png)
            $report.Screenshots += [pscustomobject]@{ Name = $name; Path = $file; Sha256 = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash }
        } finally {
            $graphics.Dispose()
            $bitmap.Dispose()
        }
    } catch {
        $report.Screenshots += [pscustomobject]@{ Name = $name; Path = $null; Error = $_.Exception.Message }
    }
    Save-Report
}

function Get-ElementPatterns($element) {
    $names = @()
    foreach ($pair in @(
            @('ValuePattern', [System.Windows.Automation.ValuePattern]::Pattern),
            @('TextPattern', [System.Windows.Automation.TextPattern]::Pattern),
            @('InvokePattern', [System.Windows.Automation.InvokePattern]::Pattern),
            @('ExpandCollapsePattern', [System.Windows.Automation.ExpandCollapsePattern]::Pattern),
            @('SelectionItemPattern', [System.Windows.Automation.SelectionItemPattern]::Pattern),
            @('TogglePattern', [System.Windows.Automation.TogglePattern]::Pattern),
            @('ScrollPattern', [System.Windows.Automation.ScrollPattern]::Pattern))) {
        $pattern = $null
        if ($element.TryGetCurrentPattern($pair[1], [ref]$pattern)) { $names += $pair[0] }
    }
    $names -join ','
}

function Read-ComposerText($composer) {
    $pattern = $null
    if ($composer.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) {
        return ([string]$pattern.Current.Value)
    }
    if ($composer.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$pattern)) {
        return ([string]$pattern.DocumentRange.GetText(-1))
    }
    return $null
}

function Get-DshWindows {
    $session = [System.Diagnostics.Process]::GetCurrentProcess().SessionId
    @(Get-Process -Name $processName -ErrorAction SilentlyContinue | Where-Object {
        $_.SessionId -eq $session -and $_.MainWindowHandle -ne [IntPtr]::Zero
    })
}

function Wait-DshWindow([int]$seconds) {
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    do {
        $windows = Get-DshWindows
        if ($windows.Count -ge 1) { return $windows }
        Start-Sleep -Milliseconds 250
    } while ($timer.Elapsed.TotalSeconds -lt $seconds)
    @()
}

function Get-UiSnapshot($window, [string]$label) {
    $snapshot = [ordered]@{
        Label              = $label
        WindowTitle        = $window.Current.Name
        WindowProcessId    = $window.Current.ProcessId
        WorkspaceLabels    = @()
        ComposerCandidates = @()
        SendCandidates     = @()
        SwitchCandidates   = @()
        VisibleEdits       = @()
        Tree               = @()
    }
    try {
        $elements = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)
    } catch {
        $snapshot.TreeError = $_.Exception.Message
        return $snapshot
    }
    $index = 0
    foreach ($element in $elements) {
        $index++
        if ($index -gt 600) { break }
        try {
            $properties = $element.Current
            $name = [string]$properties.Name
            $type = $properties.ControlType
            $bounds = $properties.BoundingRectangle
            $row = [pscustomobject]@{
                Index        = $index
                Name         = $name
                ControlType  = $type.ProgrammaticName
                AutomationId = [string]$properties.AutomationId
                ClassName    = [string]$properties.ClassName
                Offscreen    = $properties.IsOffscreen
                Enabled      = $properties.IsEnabled
                Patterns     = Get-ElementPatterns $element
                Bounds       = "$([int]$bounds.X),$([int]$bounds.Y),$([int]$bounds.Width),$([int]$bounds.Height)"
            }
            $snapshot.Tree += $row
            if ($name -like "*$($request.WorkspaceLabel)*") { $snapshot.WorkspaceLabels += $row }
            if (-not $properties.IsOffscreen -and
                $name -match '描述你想要构建|发送消息或创建任务|Describe what you|Send a message or create') {
                $snapshot.ComposerCandidates += $row
            }
            if (-not $properties.IsOffscreen -and $type -eq [System.Windows.Automation.ControlType]::Button -and
                $name.Trim() -in @('发送消息', '发送', 'Send message', 'Send')) {
                $snapshot.SendCandidates += $row
            }
            if (-not $properties.IsOffscreen -and
                $name -match '工作区|工作目录|Workspace|打开文件夹|Open folder|选择文件夹|Select folder|切换') {
                $snapshot.SwitchCandidates += $row
            }
            if ($type -eq [System.Windows.Automation.ControlType]::Edit -and -not $properties.IsOffscreen) {
                $snapshot.VisibleEdits += $row
            }
        } catch [System.Windows.Automation.ElementNotAvailableException] {
            continue
        } catch {
            continue
        }
    }
    if ($snapshot.ComposerCandidates.Count -eq 1) {
        $composer = $null
        $match = $snapshot.ComposerCandidates[0]
        $all = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)
        if ($all.Count -ge $match.Index) {
            $composer = $all.Item($match.Index - 1)
            $snapshot.ComposerText = Read-ComposerText $composer
            $snapshot.ComposerFocusable = [bool]$composer.Current.IsKeyboardFocusable
        }
    }
    if ($snapshot.SendCandidates.Count -ge 1) {
        $snapshot.SendEnabled = @($snapshot.SendCandidates | ForEach-Object { $_.Enabled })
    }
    $snapshot
}

function Get-WindowText($window, [int]$limit = 800) {
    $parts = @()
    try {
        $all = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
        $index = 0
        foreach ($element in $all) {
            $index++
            if ($index -gt 200) { break }
            try {
                $name = [string]$element.Current.Name
                if ($name) { $parts += ('{0}:{1}' -f $element.Current.ControlType.ProgrammaticName.Split('.')[-1], $name) }
            } catch { }
        }
    } catch { }
    $text = ($parts -join ' | ')
    if ($text.Length -gt $limit) { $text = $text.Substring(0, $limit) }
    $text
}

function Get-TopWindows([int]$processId, [switch]$All) {
    $rows = @()
    try {
        $root = [System.Windows.Automation.AutomationElement]::RootElement
        foreach ($window in $root.FindAll([System.Windows.Automation.TreeScope]::Children,
                [System.Windows.Automation.Condition]::TrueCondition)) {
            try {
                $className = [string]$window.Current.ClassName
                $owner = $window.Current.ProcessId
                if (-not $All -and $owner -ne $processId -and $className -ne '#32770') { continue }
                $name = [string]$window.Current.Name
                if ($All -and -not $name -and $className -ne '#32770') { continue }
                $rows += [pscustomobject]@{
                    Name        = $name
                    Class       = $className
                    ProcessId   = $owner
                    ControlType = $window.Current.ControlType.ProgrammaticName
                    Text        = (Get-WindowText $window)
                }
            } catch { }
        }
    } catch { }
    @($rows)
}

# A folder picker owned by an Electron app is a real Win32 dialog, but it is not always
# reachable through the UI Automation desktop tree. Enumerating HWNDs directly finds it.
function Get-DialogElement([int]$processId, [int]$seconds) {
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    do {
        try {
            foreach ($entry in [NativeWindowList]::List()) {
                if (-not $entry.Visible) { continue }
                $isDialog = $entry.Class -eq '#32770'
                $isPicker = $entry.Title -match '(?i)选择文件夹|浏览文件夹|Select Folder|Open Folder'
                if (-not $isDialog -and -not $isPicker) { continue }
                if ($processId -gt 0 -and $entry.ProcessId -ne $processId -and -not $isPicker) { continue }
                try { return [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$entry.Handle) }
                catch { }
            }
        } catch { }
        Start-Sleep -Milliseconds 400
    } while ($timer.Elapsed.TotalSeconds -lt $seconds)
    $null
}

function Open-WorkspacePicker($window, [int]$processId) {
    $result = [ordered]@{ Expanded = $false; Error = $null; Before = @(); After = @(); NewWindows = @() }
    $target = $null
    $all = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($element in $all) {
        try {
            if ($element.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and
                [string]$element.Current.Name -eq '选择工作区') { $target = $element; break }
        } catch { }
    }
    if (-not $target) { $result.Error = 'The workspace picker button is missing.'; return $result }
    $result.Before = Get-TopWindows $processId
    $pattern = $null
    if (-not $target.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$pattern)) {
        $result.Error = 'The workspace picker exposes no expand pattern.'
        return $result
    }
    try { $pattern.Expand(); $result.Expanded = $true }
    catch { $result.Error = $_.Exception.Message; return $result }
    Start-Sleep -Milliseconds 1500
    $result.After = Get-TopWindows $processId
    $beforeKeys = @($result.Before | ForEach-Object { '{0}|{1}|{2}' -f $_.ProcessId, $_.Class, $_.Name })
    $result.NewWindows = @($result.After | Where-Object {
            $beforeKeys -notcontains ('{0}|{1}|{2}' -f $_.ProcessId, $_.Class, $_.Name)
        })
    Save-Screenshot 'after-workspace-picker'
    try { $pattern.Collapse() } catch { }
    $result
}

function Get-StateDiscovery([string]$workspace) {
    $result = [ordered]@{ Directories = @(); FilesWithWorkspaceKey = @(); WorkspacePathMentioned = @() }
    $roots = @()
    foreach ($base in @($env:APPDATA, $env:LOCALAPPDATA)) {
        if (-not $base) { continue }
        foreach ($candidate in @(Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match '(?i)harness|deepseek' })) {
            $roots += $candidate.FullName
        }
    }
    foreach ($dir in $roots) {
        $files = @(Get-ChildItem -LiteralPath $dir -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Length -lt 1MB } | Select-Object -First 300)
        $result.Directories += [pscustomobject]@{
            Path  = $dir
            Count = $files.Count
            Files = @($files | Select-Object -First 60 | ForEach-Object {
                    '{0} ({1} B)' -f $_.FullName.Substring($dir.Length + 1), $_.Length
                })
        }
        foreach ($file in $files) {
            if ($file.Extension -notin @('.json', '.jsonc', '.yaml', '.yml', '.ini', '.log', '.txt')) { continue }
            try {
                $text = [IO.File]::ReadAllText($file.FullName)
                if ($text -match '(?i)workspace') {
                    if ($result.FilesWithWorkspaceKey.Count -lt 8) {
                        $snippets = [regex]::Matches($text, '(?i).{0,50}workspace.{0,90}') |
                            Select-Object -First 5 | ForEach-Object { $_.Value }
                        $result.FilesWithWorkspaceKey += [pscustomobject]@{ File = $file.FullName; Snippets = @($snippets) }
                    }
                }
                if ($workspace -and $text.Contains($workspace)) {
                    $result.WorkspacePathMentioned += $file.FullName
                }
            } catch { }
        }
    }
    $result
}

# Expands the in-page workspace picker, chooses "添加工作区…", types the run workspace
# into the native folder dialog and confirms it. No message is typed or sent.
function Select-WorkspaceViaPicker($window, [int]$processId, [string]$workspace) {
    $result = [ordered]@{
        Expanded       = $false
        AddMenuFound   = $false
        SidebarTriggered = $false
        WindowsAfterInvoke = @()
        WindowsAfterSidebar = @()
        TreeAfterInvoke = @()
        Clicked        = $false
        TreeAfterClick = @()
        WindowsAfterClick = @()
        DialogFound    = $false
        DialogClass    = $null
        DialogText     = $null
        KeyboardEntry  = $null
        EditName       = $null
        EditSet        = $false
        ConfirmButton  = $null
        ConfirmInvoked = $false
        ClickOutcome   = $null
        DialogClosed   = $false
        WorkspaceLabelSeen = $false
        Error          = $null
    }
    $picker = $null
    foreach ($element in $window.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)) {
        try {
            if ($element.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and
                [string]$element.Current.Name -eq '选择工作区') { $picker = $element; break }
        } catch { }
    }
    if (-not $picker) { $result.Error = 'The workspace picker button is missing.'; return $result }
    $pattern = $null
    if (-not $picker.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$pattern)) {
        $result.Error = 'The workspace picker exposes no expand pattern.'
        return $result
    }
    try { $pattern.Expand(); $result.Expanded = $true }
    catch { $result.Error = $_.Exception.Message; return $result }
    Start-Sleep -Milliseconds 900

    $addItem = $null
    $fallbackItem = $null
    foreach ($element in $window.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)) {
        try {
            $name = [string]$element.Current.Name
            if ($name -notlike '添加工作区*') { continue }
            if ($element.Current.ControlType -eq [System.Windows.Automation.ControlType]::MenuItem) { $addItem = $element; break }
            if (-not $fallbackItem) { $fallbackItem = $element }
        } catch { }
    }
    if (-not $addItem) { $addItem = $fallbackItem }
    if (-not $addItem) { $result.Error = 'The add-workspace menu item is missing.'; return $result }
    $result.AddMenuFound = $true
    $invoke = $null
    if ($addItem.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$invoke)) {
        $invoke.Invoke()
    } else {
        $selection = $null
        if ($addItem.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$selection)) {
            $selection.Select()
        } else {
            $result.Error = 'The add-workspace menu item exposes no action.'
            return $result
        }
    }

    Start-Sleep -Milliseconds 1200
    $result.WindowsAfterInvoke = @(Get-TopWindows 0 -All)
    $result.TreeAfterInvoke = @(Get-WindowNodeList $window 200)
    Save-Screenshot 'after-add-workspace'

    $dialog = Get-DialogElement $processId 8
    if (-not $dialog) {
        # InvokePattern is frequently a no-op inside Chromium, so a real click is tried next.
        $result.Clicked = Click-Element $addItem
        Start-Sleep -Milliseconds 1600
        $result.TreeAfterClick = @(Get-WindowNodeList $window 200)
        $result.WindowsAfterClick = @(Get-TopWindows 0 -All)
        Save-Screenshot 'after-add-workspace-click'
        $dialog = Get-DialogElement $processId 8
    }
    $dialog = Get-DialogElement $processId 12
    if (-not $dialog) {
        # The menu entry may only re-focus the sidebar; try that control as a second trigger.
        $report.Stage = 'workspace_selection_fallback'
        $sidebar = $null
        foreach ($element in $window.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                [System.Windows.Automation.Condition]::TrueCondition)) {
            try {
                if ($element.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and
                    [string]$element.Current.Name -eq '添加工作区') { $sidebar = $element; break }
            } catch { }
        }
        if ($sidebar) {
            $result.SidebarTriggered = $true
            $invokeSidebar = $null
            if ($sidebar.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$invokeSidebar)) {
                $invokeSidebar.Invoke()
            }
            Start-Sleep -Milliseconds 1500
            $result.WindowsAfterSidebar = @(Get-TopWindows 0 -All)
            Save-Screenshot 'after-add-workspace-sidebar'
            $dialog = Get-DialogElement $processId 12
        }
    }
    if (-not $dialog) { $result.Error = 'The folder dialog did not appear.'; return $result }
    $result.DialogFound = $true
    $result.DialogClass = [string]$dialog.Current.ClassName
    Start-Sleep -Milliseconds 800
    $result.DialogText = Get-WindowText $dialog 500
    $handle = [intptr]$dialog.Current.NativeWindowHandle

    # 1) Enter the path through the address bar (Ctrl+L, Unicode input, Enter) and confirm
    # it there. That step reports its own verification, so the outcome is relayed as is.
    $result.KeyboardEntry = Set-DialogPath $dialog $workspace
    $result.PathField = $result.KeyboardEntry.FolderField
    $result.ConfirmButton = $result.KeyboardEntry.ConfirmButton
    $result.ClickOutcome = $result.KeyboardEntry.ClickOutcome
    if ($result.KeyboardEntry.Error -and -not $result.Error) { $result.Error = $result.KeyboardEntry.Error }
    $result.DialogClosed = $result.KeyboardEntry.DialogClosed
    Start-Sleep -Milliseconds 1200

    # 3) Verify against the closed dialog only: the picker's own tree also contains the
    # path, so checking before it closes would report a false success.
    Start-Sleep -Seconds 2
    Save-Screenshot 'after-workspace-selection'
    $leaf = Split-Path -Leaf $workspace
    $labels = @()
    if ($result.DialogClosed) {
        foreach ($element in $window.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                [System.Windows.Automation.Condition]::TrueCondition)) {
            try {
                if ([string]$element.Current.Name -eq $leaf) { $labels += [string]$element.Current.Name }
            } catch { }
        }
    }
    $result.WorkspaceLabelSeen = $labels.Count -ge 1
    $result
}

function Get-WindowNodeList($window, [int]$limit = 90) {
    $rows = @()
    try {
        $all = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
        $index = 0
        foreach ($element in $all) {
            $index++
            if ($index -gt $limit) { break }
            try {
                $rows += ('{0}:{1}' -f $element.Current.ControlType.ProgrammaticName.Split('.')[-1], [string]$element.Current.Name)
            } catch { }
        }
    } catch { }
    @($rows)
}

# Chromium pages often ignore InvokePattern on menu items, so a real pointer click at the
# element's centre is the fallback. What sits under that point is checked before the pointer
# moves: a click that lands on a neighbouring control looks exactly like a click that worked.
function Click-Element($element) {
    try {
        $bounds = $element.Current.BoundingRectangle
        $width = [double]$bounds.Width
        $height = [double]$bounds.Height
        if ([double]::IsInfinity($width) -or [double]::IsInfinity($height) -or $width -le 1 -or $height -le 1) {
            return 'The element has no clickable bounds (it is already off screen).'
        }
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $x = [int][math]::Round([double]$bounds.X + $width / 2)
        $y = [int][math]::Round([double]$bounds.Y + $height / 2)
        $hitName = $null
        $onTarget = $false
        try {
            $hit = [System.Windows.Automation.AutomationElement]::FromPoint((New-Object System.Drawing.Point($x, $y)))
            if ($hit) {
                $hitName = [string]$hit.Current.Name
                $onTarget = ($hit -eq $element)
                if (-not $onTarget -and -not $hitName) { $hitName = [string]$hit.Current.ControlType.ProgrammaticName }
            }
        } catch { }
        [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point($x, $y)
        Start-Sleep -Milliseconds 150
        [Win32.NativeMouse]::mouse_event(0x0002, 0, 0, 0, 0)
        Start-Sleep -Milliseconds 60
        [Win32.NativeMouse]::mouse_event(0x0004, 0, 0, 0, 0)
        return ('clicked x={0} y={1} hit={2} onTarget={3}' -f $x, $y, $hitName, $onTarget)
    } catch { return "click failed: $($_.Exception.Message)" }
}

# The dialog's bottom "文件夹:" row only accepts a folder that is already displayed, so the
# path is entered through the address bar instead: Ctrl+L focuses it, the path is injected as
# Unicode keyboard input, and Enter navigates into the folder.
function Get-AddressPaneText($dialog) {
    foreach ($element in $dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)) {
        try {
            $name = [string]$element.Current.Name
            if ($name -match '^地址\s*[:：]') { return $name }
        } catch { }
    }
    return $null
}

function Test-DialogPathMatch([string]$text, [string]$workspace) {
    if (-not $text) { return $false }
    $value = ($text -replace '^地址\s*[:：]\s*', '').Trim().Trim('\').ToLowerInvariant()
    $full = $workspace.Trim().TrimEnd('\').ToLowerInvariant()
    if ($value -eq $full) { return $true }
    return $value -eq (Split-Path -Leaf $workspace).ToLowerInvariant()
}

function Set-DialogPath($dialog, [string]$workspace) {
    $result = [ordered]@{
        Foreground     = $false
        AddressBefore  = $null
        AddressAfter   = $null
        FolderField    = $null
        ItemNames      = @()
        AddressMatched = $false
        UnicodeTyping  = $false
        Navigated      = $false
        ConfirmButton  = $null
        ClickOutcome   = $null
        DialogClosed   = $false
        Sent           = @()
        Error          = $null
    }
    try {
        $handle = [intptr]$dialog.Current.NativeWindowHandle
        $result.Foreground = [NativeWindowList]::SetForegroundWindow($handle)
        Start-Sleep -Milliseconds 500
        Add-Type -AssemblyName System.Windows.Forms
        $result.AddressBefore = Get-AddressPaneText $dialog

        # 1) Ctrl+L focuses the address bar and selects its current path.
        [System.Windows.Forms.SendKeys]::SendWait('^l')
        $result.Sent += 'ctrl+l'
        Start-Sleep -Milliseconds 800

        # 2) Replace the selected path with the run workspace, bypassing the IME.
        $result.UnicodeTyping = [NativeInput]::Text($workspace)
        $result.Sent += 'unicode-type'
        Start-Sleep -Milliseconds 900
        Save-Screenshot 'after-path-typed'

        # 3) Enter navigates into that folder.
        [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
        $result.Sent += 'enter-navigate'
        Start-Sleep -Milliseconds 1700
        $result.AddressAfter = Get-AddressPaneText $dialog
        $items = @()
        foreach ($element in $dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                [System.Windows.Automation.Condition]::TrueCondition)) {
            try {
                if ([string]$element.Current.AutomationId -eq '1152') { $result.FolderField = [string]$element.Current.Name }
                if ($element.Current.ControlType -eq [System.Windows.Automation.ControlType]::ListItem) {
                    $items += [string]$element.Current.Name
                }
            } catch { }
        }
        $result.ItemNames = @($items | Select-Object -First 12)
        Save-Screenshot 'after-path-navigate'
        # The scratch workspace always holds input\ and output\, so seeing both proves the
        # list really moved into the requested folder even when the address text is stale.
        $listProves = ($items -contains 'input') -and ($items -contains 'output')
        $result.AddressMatched = (Test-DialogPathMatch $result.AddressAfter $workspace) -or
            (Test-DialogPathMatch $result.FolderField $workspace) -or $listProves
        $result.Navigated = $result.AddressMatched

        # 4) Confirm, but only once a field actually shows the requested folder.
        if ($result.AddressMatched -and (Test-DialogOpen $handle)) {
            $confirm = $null
            foreach ($element in $dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                    [System.Windows.Automation.Condition]::TrueCondition)) {
                try {
                    if ([string]$element.Current.Name -eq '选择文件夹') { $confirm = $element; break }
                } catch { }
            }
            if ($confirm) {
                $result.ConfirmButton = [string]$confirm.Current.Name
                $result.ClickOutcome = Click-Element $confirm
                Start-Sleep -Milliseconds 1500
            } else {
                $result.Error = 'The folder dialog exposes no confirm control.'
            }
        } elseif (-not $result.AddressMatched) {
            $result.Error = 'The address bar did not resolve to the requested workspace.'
            [System.Windows.Forms.SendKeys]::SendWait('{ESC}')
            $result.Sent += 'esc'
            Start-Sleep -Milliseconds 600
        }
        $result.DialogClosed = -not (Test-DialogOpen $handle)
    } catch {
        $result.Error = $_.Exception.Message
        try { [System.Windows.Forms.SendKeys]::SendWait('{ESC}') } catch { }
    }
    $result
}

function Test-DialogOpen([intptr]$handle) {
    try {
        foreach ($entry in [NativeWindowList]::List()) {
            if ($entry.Handle -eq $handle.ToInt64() -and $entry.Visible) { return $true }
        }
    } catch { }
    return $false
}

function Stop-DshProcess {
    $session = [System.Diagnostics.Process]::GetCurrentProcess().SessionId
    $targets = @(Get-Process -Name $processName -ErrorAction SilentlyContinue | Where-Object SessionId -eq $session)
    foreach ($target in $targets) { Stop-Process -Id $target.Id -Force -ErrorAction SilentlyContinue }
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    do {
        if (@(Get-Process -Name $processName -ErrorAction SilentlyContinue | Where-Object SessionId -eq $session).Count -eq 0) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    } while ($timer.Elapsed.TotalSeconds -lt 15)
    return $false
}

function Record-Attempt([string]$name, $snapshot, [int]$windowCount, [string]$note) {
    $match = 0
    $composerCount = 0
    $sendCount = 0
    $sendEnabled = $null
    $composerEmpty = $null
    if ($snapshot) {
        $match = @($snapshot.WorkspaceLabels).Count
        $composerCount = @($snapshot.ComposerCandidates).Count
        $sendCount = @($snapshot.SendCandidates).Count
        if ($sendCount -ge 1) { $sendEnabled = $snapshot.SendCandidates[0].Enabled }
        if ($composerCount -eq 1 -and $null -ne $snapshot.ComposerText) {
            $composerEmpty = ([string]$snapshot.ComposerText).Trim().Length -eq 0
        }
    }
    $record = [pscustomobject]@{
        Name                = $name
        At                  = (Get-Date).ToString('o')
        WindowCount         = $windowCount
        WorkspaceLabelCount = $match
        ComposerCount       = $composerCount
        SendCount           = $sendCount
        SendEnabled         = $sendEnabled
        ComposerEmpty       = $composerEmpty
        Note                = $note
        Snapshot            = $snapshot
    }
    $report.Attempts += $record
    Save-Report
    $record
}

# ------------------------------------------------------------- task dispatch (task mode)
# The task request is frozen on the host before the machine is touched. This side only
# pastes it, reads it back, and waits for the host to authorise a single send.

function Get-Sha256Text([string]$text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($text)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', '').ToLowerInvariant()
    } finally { $sha.Dispose() }
}

function Get-ComposerElement($window) {
    foreach ($element in $window.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)) {
        try {
            if ($element.Current.IsOffscreen) { continue }
            $name = [string]$element.Current.Name
            if ($name -match '描述你想要构建|发送消息或创建任务|Describe what you|Send a message or create') {
                return $element
            }
        } catch { }
    }
    return $null
}

function Get-SendButtons($window) {
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Button)
    foreach ($element in $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)) {
        if (-not $element.Current.IsOffscreen -and
            ([string]$element.Current.Name).Trim() -in @('发送消息', '发送', 'Send message', 'Send')) {
            $element
        }
    }
}

function Get-ComposerLength([string]$text) {
    if ($null -eq $text) { return -1 }
    return ([string]$text).Trim().Length
}

# A run that was interrupted leaves its prompt behind, and typing the next prompt on top of it
# produces a readback of two prompts. Select-all is the cheap route; a rich editor can swallow
# that chord, so the caret is parked at the end and the keys are deleted one at a time after it.
function Reset-Composer($composer) {
    $result = [ordered]@{
        Before = 0; After = 0; RawBefore = 0; RawAfter = 0
        Route = 'unknown'; Cleared = $false; Steps = @()
    }
    $text = Read-ComposerText $composer
    if ($null -eq $text) { $result.Route = 'unreadable'; return $result }
    $result.RawBefore = ([string]$text).Length
    $result.Before = Get-ComposerLength $text
    if ($result.Before -eq 0) { $result.Cleared = $true; $result.Route = 'already-empty'; return $result }

    try { $composer.SetFocus() } catch { }
    Start-Sleep -Milliseconds 200
    $null = [NativeInput]::Clear()
    Start-Sleep -Milliseconds 450
    $after = Read-ComposerText $composer
    if ($null -eq $after) { $result.Route = 'unreadable-after-clear'; return $result }
    $result.RawAfter = ([string]$after).Length
    $result.After = Get-ComposerLength $after
    $result.Steps += ('select-all+delete:{0}/{1}' -f $result.After, $result.RawAfter)
    if ($result.After -eq 0) { $result.Cleared = $true; $result.Route = 'select-all'; return $result }

    $null = [NativeInput]::MoveEnd()
    Start-Sleep -Milliseconds 250
    $presses = [math]::Min($result.RawAfter + 16, 600)
    $null = [NativeInput]::Backspace($presses)
    Start-Sleep -Milliseconds 700
    $final = Read-ComposerText $composer
    if ($null -eq $final) { $result.Route = 'unreadable-after-backspace'; return $result }
    $result.RawAfter = ([string]$final).Length
    $result.After = Get-ComposerLength $final
    $result.Steps += ('backspace:{0}/{1}' -f $result.After, $result.RawAfter)
    if ($result.After -eq 0) { $result.Cleared = $true; $result.Route = 'backspace' } else { $result.Route = 'failed' }
    $result
}

function Invoke-TaskDispatch($window, $taskRequest, [string]$outDirectory) {
    $result = [ordered]@{
        PromptSha256        = $null
        PromptLength        = 0
        PromptMatches       = $false
        ReadbackSha256      = $null
        ReadbackLength      = 0
        ReadbackPreview     = $null
        ComposerLeftoverLength = 0
        ComposerRawLeftoverLength = 0
        ComposerCleared     = $false
        ComposerClearRoute  = $null
        ComposerClearSteps  = @()
        ComposerLengthAfterClear = $null
        ComposerRawLengthAfterClear = $null
        Workspace           = $taskRequest.Workspace
        ReadyAt             = $null
        GoReceivedAt        = $null
        SendControlName     = $null
        InvokeStartedAt     = $null
        InvokeReturnedAt    = $null
        Error               = $null
    }
    $report.Dispatch = $result
    try {
        $prompt = [IO.File]::ReadAllText($taskRequest.PromptFile, [Text.UTF8Encoding]::new($false))
        $result.PromptLength = $prompt.Length
        $result.PromptSha256 = Get-Sha256Text $prompt

        $composer = Get-ComposerElement $window
        if (-not $composer) { $result.Error = 'The composer is missing after the workspace was chosen.'; return $result }
        try { $composer.SetFocus() } catch { }
        $null = Click-Element $composer
        Start-Sleep -Milliseconds 400

        # DSH keeps whatever a previous session left in the composer. Typing on top of that debris
        # produces a readback of two prompts, so the field is emptied and read back as empty first.
        $reset = Reset-Composer $composer
        $result.ComposerLeftoverLength = $reset.Before
        $result.ComposerRawLeftoverLength = $reset.RawBefore
        $result.ComposerClearRoute = $reset.Route
        $result.ComposerClearSteps = @($reset.Steps)
        $result.ComposerCleared = $reset.Cleared
        $result.ComposerLengthAfterClear = $reset.After
        $result.ComposerRawLengthAfterClear = $reset.RawAfter
        if ($reset.Before -gt 0) { Save-Screenshot 'after-composer-clear' }
        if (-not $reset.Cleared) {
            $result.Error = "The composer could not be emptied (route=$($reset.Route), remaining=$($reset.After)); nothing was sent."
            return $result
        }

        if (-not [NativeInput]::Text($prompt)) {
            $result.Error = 'The prompt could not be injected into the composer.'
            return $result
        }
        Start-Sleep -Milliseconds 1200
        $readback = Read-ComposerText $composer
        $result.ReadbackLength = if ($readback) { $readback.Length } else { 0 }
        $result.ReadbackSha256 = Get-Sha256Text ([string]$readback)
        $preview = [string]$readback
        if ($preview.Length -gt 240) { $preview = $preview.Substring(0, 240) }
        $result.ReadbackPreview = $preview
        $result.PromptMatches = ($result.ReadbackSha256 -eq $result.PromptSha256)
        Save-Screenshot 'after-prompt-paste'
        $result.ReadyAt = (Get-Date).ToString('o')
        $readyPath = Join-Path $outDirectory 'ready.json'
        [IO.File]::WriteAllText("$readyPath.tmp",
            ([pscustomobject]@{
                Workspace      = $taskRequest.Workspace
                PromptMatches  = $result.PromptMatches
                PromptSha256   = $result.PromptSha256
                PromptReadbackSha256 = $result.ReadbackSha256
                PromptLength   = $result.PromptLength
                ReadbackLength = $result.ReadbackLength
                ReadbackPreview = $result.ReadbackPreview
                ComposerLeftoverLength = $result.ComposerLeftoverLength
                ComposerCleared = $result.ComposerCleared
                ComposerClearRoute = $result.ComposerClearRoute
                ComposerClearSteps = @($result.ComposerClearSteps)
                ComposerLengthAfterClear = $result.ComposerLengthAfterClear
                ReadyAt        = $result.ReadyAt
            } | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
        [IO.File]::Move("$readyPath.tmp", $readyPath)
        if (-not $result.PromptMatches) {
            $result.Error = 'The composer readback does not match the frozen prompt; nothing was sent.'
            return $result
        }

        # No send may happen until the host has recorded the submission permit.
        $goPath = Join-Path $outDirectory 'go.json'
        Add-Step 'await_send_authorisation' 'waiting' $goPath
        $timer = [System.Diagnostics.Stopwatch]::StartNew()
        while (-not (Test-Path -LiteralPath $goPath -PathType Leaf)) {
            if ($timer.Elapsed.TotalSeconds -ge [int]$taskRequest.GoTimeoutSeconds) {
                $result.Error = 'The host did not authorise the send; nothing was sent.'
                return $result
            }
            Start-Sleep -Milliseconds 500
        }
        $authorisation = Get-Content -LiteralPath $goPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($authorisation.request_id -cne $taskRequest.RunId -or
            -not $authorisation.permit_id -or $authorisation.prompt_sha256 -cne $result.PromptSha256) {
            throw 'The send authorisation does not match this request and prompt.'
        }
        $result.GoReceivedAt = (Get-Date).ToString('o')
        if ($taskRequest.IsolationProbe) {
            $recheck = Get-GuestIsolationFacts $taskRequest.IsolationProbe
            Add-Step 'isolation_before_send' 'checked' $recheck
            Assert-GuestIsolationFacts $taskRequest.IsolationProbe $recheck
        }
        Add-Step 'prepare_send_button' 'started' $result.GoReceivedAt
        $buttons = @(Get-SendButtons $window)
        if ($buttons.Count -ne 1 -or -not $buttons[0].Current.IsEnabled) {
            throw 'A unique, enabled send button was not found.'
        }
        $send = $buttons[0]
        $result.SendControlName = [string]$send.Current.Name
        $pattern = $null
        if (-not $send.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
            throw 'The send button does not support InvokePattern.'
        }
        if ((Read-ComposerText $composer) -cne $prompt -or -not $send.Current.IsEnabled) {
            throw 'The composer or send button changed after the prompt was read back.'
        }

        # Record the attempt before crossing into the UI. Never try another action after this point.
        $result.InvokeStartedAt = (Get-Date).ToString('o')
        Add-Step 'invoke_send' 'started' $result.SendControlName
        $pattern.Invoke()
        $result.InvokeReturnedAt = (Get-Date).ToString('o')
        Add-Step 'invoke_send' 'returned' $result.InvokeReturnedAt
        Save-Screenshot 'after-send'
    } catch {
        $result.Error = $_.Exception.Message
    }
    $result
}

$transcriptPath = Join-Path $probeHome 'probe-transcript.txt'
try { Start-Transcript -LiteralPath $transcriptPath -Force | Out-Null } catch { }

try {
    if ($TaskMode -and $request.IsolationProbe) {
        $isolationSource = [IO.File]::ReadAllText((Join-Path $probeHome 'task_isolation_probe.ps1'))
        . ([scriptblock]::Create($isolationSource))
        $isolation = [ordered]@{ Verdict = 'blocked'; Facts = $null; Error = $null }
        try {
            $isolation.Facts = Get-GuestIsolationFacts $request.IsolationProbe
            Assert-GuestIsolationFacts $request.IsolationProbe $isolation.Facts
            $isolation.Verdict = 'pass'
        } catch {
            $isolation.Error = $_.Exception.Message
        }
        $isolationPath = Join-Path $reportDirectory 'isolation-read.json'
        [IO.File]::WriteAllText("$isolationPath.tmp", ($isolation | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
        [IO.File]::Move("$isolationPath.tmp", $isolationPath)
        if ($isolation.Verdict -cne 'pass') { throw $isolation.Error }
    }

    # A scheduled-task process is DPI-unaware by default. UI Automation then reports physical
    # pixels while the pointer lands in logical ones, so on a scaled desktop every click misses
    # its target. Declaring awareness first keeps both coordinate systems identical.
    try {
        Add-Type -Namespace Win32 -Name DpiAware -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
'@
        $null = [Win32.DpiAware]::SetProcessDPIAware()
    } catch { }
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    try {
        Add-Type -Namespace Win32 -Name NativeMouse -MemberDefinition @'
[DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, int extra);
'@
    } catch { }
    # Plain SendKeys goes through the Chinese IME, which rewrote "C:\Users" as "C：、Users".
    # Injected KEYEVENTF_UNICODE input bypasses the IME entirely.
    try {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public class NativeInput
{
    [StructLayout(LayoutKind.Sequential)]
    public struct MOUSEINPUT
    {
        public int dx; public int dy; public uint mouseData; public uint dwFlags;
        public uint time; public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT
    {
        public ushort wVk; public ushort wScan; public uint dwFlags;
        public uint time; public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Explicit)]
    public struct InputUnion
    {
        [FieldOffset(0)] public MOUSEINPUT mi;
        [FieldOffset(0)] public KEYBDINPUT ki;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT
    {
        public uint type;
        public InputUnion u;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint SendInput(uint count, INPUT[] inputs, int size);

    public const uint INPUT_KEYBOARD = 1;
    public const uint KEYEVENTF_UNICODE = 0x0004;
    public const uint KEYEVENTF_KEYUP = 0x0002;

    public static bool Text(string text)
    {
        if (string.IsNullOrEmpty(text)) return false;
        var inputs = new INPUT[text.Length * 2];
        int index = 0;
        foreach (char character in text)
        {
            inputs[index++] = new INPUT
            {
                type = INPUT_KEYBOARD,
                u = new InputUnion { ki = new KEYBDINPUT { wVk = 0, wScan = (ushort)character, dwFlags = KEYEVENTF_UNICODE } }
            };
            inputs[index++] = new INPUT
            {
                type = INPUT_KEYBOARD,
                u = new InputUnion { ki = new KEYBDINPUT { wVk = 0, wScan = (ushort)character, dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP } }
            };
        }
        uint sent = SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(INPUT)));
        return sent == (uint)inputs.Length;
    }

    // A composer that still holds text from an earlier run turns the readback into leftover text
    // plus the frozen prompt. Select-all then delete leaves the field genuinely empty.
    public static bool Clear()
    {
        var inputs = new INPUT[6];
        inputs[0] = VirtualKey(0x11, false); // Ctrl
        inputs[1] = VirtualKey(0x41, false); // A
        inputs[2] = VirtualKey(0x41, true);
        inputs[3] = VirtualKey(0x11, true);
        inputs[4] = VirtualKey(0x2E, false); // Delete
        inputs[5] = VirtualKey(0x2E, true);
        uint sent = SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(INPUT)));
        return sent == (uint)inputs.Length;
    }

    private static INPUT VirtualKey(ushort key, bool up)
    {
        return new INPUT
        {
            type = INPUT_KEYBOARD,
            u = new InputUnion
            {
                ki = new KEYBDINPUT
                {
                    wVk = key,
                    wScan = 0,
                    dwFlags = up ? KEYEVENTF_KEYUP : 0u
                }
            }
        };
    }

    // Ctrl+End parks the caret after the last character so deletions always eat the real text.
    public static bool MoveEnd()
    {
        var inputs = new INPUT[4];
        inputs[0] = VirtualKey(0x11, false);
        inputs[1] = VirtualKey(0x23, false);
        inputs[2] = VirtualKey(0x23, true);
        inputs[3] = VirtualKey(0x11, true);
        uint sent = SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(INPUT)));
        return sent == (uint)inputs.Length;
    }

    // A rich editor that swallows Ctrl+A still honours the delete key. The keys go out in small
    // batches because the input queue drops anything that arrives faster than it drains.
    public static bool Backspace(int count)
    {
        if (count <= 0) return false;
        const int batch = 40;
        int remaining = count;
        bool ok = true;
        while (remaining > 0)
        {
            int n = Math.Min(batch, remaining);
            var inputs = new INPUT[n * 2];
            int index = 0;
            for (int i = 0; i < n; i++)
            {
                inputs[index++] = VirtualKey(0x08, false);
                inputs[index++] = VirtualKey(0x08, true);
            }
            uint sent = SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(INPUT)));
            ok = ok && sent == (uint)inputs.Length;
            remaining -= n;
            System.Threading.Thread.Sleep(25);
        }
        return ok;
    }
}
'@
    } catch { }
    try {
        Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public class NativeWindowList
{
    public class Entry
    {
        public long Handle;
        public string Class;
        public string Title;
        public uint ProcessId;
        public bool Visible;
    }

    private delegate bool EnumProc(IntPtr handle, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc callback, IntPtr parameter);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(IntPtr handle, StringBuilder text, int count);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr handle, StringBuilder text, int count);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr handle);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr handle, out uint processId);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr handle);

    public static List<Entry> List()
    {
        var list = new List<Entry>();
        EnumWindows(delegate(IntPtr handle, IntPtr parameter)
        {
            var cls = new StringBuilder(256);
            GetClassName(handle, cls, 256);
            var title = new StringBuilder(512);
            GetWindowText(handle, title, 512);
            uint owner;
            GetWindowThreadProcessId(handle, out owner);
            var item = new Entry();
            item.Handle = handle.ToInt64();
            item.Class = cls.ToString();
            item.Title = title.ToString();
            item.ProcessId = owner;
            item.Visible = IsWindowVisible(handle);
            list.Add(item);
            return true;
        }, IntPtr.Zero);
        return list;
    }
}
'@
    } catch { }

    $report.Stage = 'observe_session'
    $sessionId = [System.Diagnostics.Process]::GetCurrentProcess().SessionId
    $explorer = @(Get-Process explorer -ErrorAction SilentlyContinue | Where-Object SessionId -eq $sessionId)
    $logonUi = @(Get-Process LogonUI -ErrorAction SilentlyContinue | Where-Object SessionId -eq $sessionId)
    $report.Session = [pscustomobject]@{
        SessionId        = $sessionId
        UserProfile      = $env:USERPROFILE
        UserName         = $env:USERNAME
        Interactive      = [Environment]::UserInteractive
        ExplorerCount    = $explorer.Count
        LogonUiPresent   = $logonUi.Count -gt 0
        DesktopLocked    = $logonUi.Count -gt 0
        DshRunningBefore = @(Get-Process -Name $processName -ErrorAction SilentlyContinue).Count
    }
    Add-Step 'observe_session' 'done' $report.Session
    if ($logonUi.Count -gt 0) {
        $report.Verdict = 'desktop_locked'
        $report.Stage = 'observe_session'
        throw 'The interactive session is locked by LogonUI. Unlock the desktop before the probe can drive UI Automation.'
    }

    # The probe only ever creates its own scratch workspace; it never touches a run workspace.
    $workspace = $request.Workspace
    if (-not (Test-Path -LiteralPath $workspace -PathType Container)) {
        throw "The preflight workspace is missing: $workspace"
    }
    # A single launch is enough: measured runs showed DSH binds neither the process working
    # directory nor a path argument, and both routes ended at the workspace picker. Launching
    # twice doubled the wall time and produced identical trees, so the second launch is gone.
    $report.Stage = 'launch'
    Add-Step 'stop_stale_dsh' ($(if (Stop-DshProcess) { 'done' } else { 'timeout' })) 'Cleared any running DSH before the launch.'

    $startedProcess = Start-Process -FilePath $request.Shortcut -WorkingDirectory $workspace -PassThru
    $windows = Wait-DshWindow 30
    $report.Stage = 'probe_after_launch'
    if ($windows.Count -ne 1) {
        Record-Attempt 'single_launch' $null $windows.Count 'Expected exactly one DSH window after launching the shortcut.' | Out-Null
        Save-Screenshot 'after-working-directory'
        throw "Expected one DSH window after the shortcut launch. Found $($windows.Count)."
    }
    $process = @(Get-Process -Name $processName | Where-Object Id -eq $windows[0].Id)[0]
    Add-Step 'launch' 'done' ([pscustomobject]@{
            ProcessId   = $process.Id
            CommandLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)").CommandLine
        })
    Start-Sleep -Seconds 2
    $window = [System.Windows.Automation.AutomationElement]::FromHandle($windows[0].MainWindowHandle)
    $first = Get-UiSnapshot $window 'single_launch'
    $firstRecord = Record-Attempt 'single_launch' $first 1 'Shortcut launched with the probe workspace as the working directory.'
    Save-Screenshot 'after-working-directory'

    if ($firstRecord.WorkspaceLabelCount -eq 1) {
        $report.Verdict = 'identified'
        $report.Stage = 'workspace_from_working_directory'
        $report.Method = 'shortcut_working_directory'
    } else {
        $report.Stage = 'insufficient'
        $report.Verdict = 'needs_workspace_selection'
        $report.Method = $null
    }

    # The launch alone did not bind the workspace, so record where DSH keeps its workspace
    # list and what the in-app picker offers. Both probes are read-only: they expand a
    # menu, capture it, and collapse it again without choosing anything.
    if ($report.Verdict -eq 'needs_workspace_selection') {
        $report.Stage = 'state_discovery'
        $report.StateDiscovery = Get-StateDiscovery $request.Workspace
        Add-Step 'state_discovery' 'done' ('files carrying a workspace key: {0}' -f @($report.StateDiscovery.FilesWithWorkspaceKey).Count)
        $report.Stage = 'workspace_picker'
        $live = @(Get-DshWindows)
        if ($live.Count -ge 1) {
            $report.WorkspacePicker = Open-WorkspacePicker $window ([int]$live[0].Id)
            Add-Step 'workspace_picker' $(if ($report.WorkspacePicker.Error) { 'failed' } else { 'done' }) $report.WorkspacePicker
        }

        $report.Stage = 'workspace_selection'
        $live = @(Get-DshWindows)
        if ($live.Count -ge 1) {
            $report.WorkspaceSelection = Select-WorkspaceViaPicker $window ([int]$live[0].Id) $request.Workspace
            Add-Step 'workspace_selection' $(if ($report.WorkspaceSelection.WorkspaceLabelSeen) { 'done' } else { 'failed' }) $report.WorkspaceSelection
            if ($report.WorkspaceSelection.WorkspaceLabelSeen) {
                $report.Verdict = 'identified'
                $report.Method = 'ui_add_workspace_dialog'
                $report.Stage = 'workspace_from_picker'
                $chosen = Get-UiSnapshot $window 'after_workspace_selection'
                $null = Record-Attempt 'after_workspace_selection' $chosen 1 'Workspace chosen through the in-app picker.'
            }
        }
    }

    # Record whether the empty composer and its disabled send button can be reached without a click.
    if ($report.Verdict -eq 'identified') {
        $report.Stage = 'focus_composer'
        try {
            $composerRow = @($report.Attempts[-1].Snapshot.ComposerCandidates)
            if ($composerRow.Count -eq 1) {
                $all = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                    [System.Windows.Automation.Condition]::TrueCondition)
                $composer = $all.Item($composerRow[0].Index - 1)
                $focusError = $null
                try { $composer.SetFocus() } catch { $focusError = $_.Exception.Message }
                Start-Sleep -Milliseconds 300
                $focused = [System.Windows.Automation.AutomationElement]::FocusedElement
                $report.Focus = [pscustomobject]@{
                    SetFocusError       = $focusError
                    FocusedAutomationId = [string]$focused.Current.AutomationId
                    FocusedName         = [string]$focused.Current.Name
                    FocusedIsComposer   = ($null -ne $focused -and
                        ($focused.GetRuntimeId() -join ',') -eq ($composer.GetRuntimeId() -join ','))
                }
                Add-Step 'focus_composer' 'done' $report.Focus
                Save-Screenshot 'after-focus'
            }
        } catch {
            $report.Focus = [pscustomobject]@{ Error = $_.Exception.Message }
            Add-Step 'focus_composer' 'failed' $report.Focus
        }
    }

    # Task mode continues past the preflight: paste the frozen prompt, read it back, wait for
    # the host authorisation, send once, then wait for the conversation to settle.
    if ($TaskMode -and $report.Verdict -eq 'identified') {
        $report.Stage = 'task_dispatch'
        Save-Report
        $report.Task = Invoke-TaskDispatch $window $request $reportDirectory
        $report.Dispatch = $report.Task
        Add-Step 'task_dispatch' $(if ($report.Task.Error) { 'failed' } else { 'done' }) $report.Task
        if ($report.Task.Error) { throw $report.Task.Error }
    }
} catch {
    $report.Error = $_.Exception.Message
    if ($report.Verdict -eq 'incomplete') { $report.Verdict = 'blocked' }
} finally {
    $report.FinishedAt = (Get-Date).ToString('o')
    Save-Report
    try { Stop-Transcript | Out-Null } catch { }
}
