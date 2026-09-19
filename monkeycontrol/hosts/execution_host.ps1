# MonkeyControl execution host: UI Automation and SendInput, in one MTA worker.
#
# Speaks JSON lines on stdin/stdout: {"id","op","args"} in, {"id","ok",...} out.
# Diagnostics go to stderr. Every coordinate here is a physical screen pixel,
# which is why the process declares itself DPI aware before it looks at a
# window. This host writes no file and knows nothing about receipts.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
# Console input decodes with the OEM code page unless it is told not to,
# which would turn every non-ASCII element name and typed character into
# mojibake before the first op ever runs.
$script:StdIn = New-Object System.IO.StreamReader(
    [Console]::OpenStandardInput(), (New-Object System.Text.UTF8Encoding($false)))

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public static class MonkeyControlDesktop
{
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    public struct MOUSEINPUT
    {
        public int dx; public int dy; public uint mouseData;
        public uint dwFlags; public uint time; public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT
    {
        public ushort wVk; public ushort wScan; public uint dwFlags;
        public uint time; public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Explicit)]
    public struct INPUTUNION
    {
        [FieldOffset(0)] public MOUSEINPUT mi;
        [FieldOffset(0)] public KEYBDINPUT ki;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT { public uint type; public INPUTUNION u; }

    public class WindowRow
    {
        public long Handle; public string Title; public int Pid;
        public int Left; public int Top; public int Right; public int Bottom;
    }

    private delegate bool EnumProc(IntPtr hwnd, IntPtr param);

    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool IsProcessDPIAware();
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hwnd, int cmd);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc proc, IntPtr param);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern int GetWindowTextLength(IntPtr hwnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int count);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
    [DllImport("user32.dll")] private static extern int GetWindowLong(IntPtr hwnd, int index);
    [DllImport("user32.dll")] private static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
    [DllImport("user32.dll", SetLastError = true)] private static extern uint SendInput(uint count, INPUT[] inputs, int size);
    [DllImport("kernel32.dll")] private static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] private static extern bool AttachThreadInput(uint from, uint to, bool attach);
    [DllImport("user32.dll")] private static extern uint MapVirtualKey(uint code, uint kind);

    private const int GWL_EXSTYLE = -20;
    private const int WS_EX_TOOLWINDOW = 0x00000080;
    private const uint GA_ROOT = 2;
    private const uint INPUT_MOUSE = 0;
    private const uint INPUT_KEYBOARD = 1;
    private const uint MOUSEEVENTF_MOVE = 0x0001;
    private const uint MOUSEEVENTF_WHEEL = 0x0800;
    private const uint MOUSEEVENTF_ABSOLUTE = 0x8000;
    private const uint MOUSEEVENTF_VIRTUALDESK = 0x4000;
    private const uint KEYEVENTF_EXTENDEDKEY = 0x0001;
    private const uint KEYEVENTF_KEYUP = 0x0002;
    private const uint KEYEVENTF_UNICODE = 0x0004;

    public static List<WindowRow> TopLevelWindows(int pid)
    {
        List<WindowRow> rows = new List<WindowRow>();
        EnumWindows(delegate(IntPtr hwnd, IntPtr param)
        {
            if (!IsWindowVisible(hwnd)) return true;
            if (GetAncestor(hwnd, GA_ROOT) != hwnd) return true;
            if ((GetWindowLong(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW) != 0) return true;
            int length = GetWindowTextLength(hwnd);
            if (length <= 0) return true;
            uint owner;
            GetWindowThreadProcessId(hwnd, out owner);
            if (pid > 0 && (int)owner != pid) return true;
            StringBuilder title = new StringBuilder(length + 1);
            GetWindowText(hwnd, title, title.Capacity);
            RECT rect;
            GetWindowRect(hwnd, out rect);
            WindowRow row = new WindowRow();
            row.Handle = hwnd.ToInt64(); row.Title = title.ToString(); row.Pid = (int)owner;
            row.Left = rect.Left; row.Top = rect.Top; row.Right = rect.Right; row.Bottom = rect.Bottom;
            rows.Add(row);
            return true;
        }, IntPtr.Zero);
        return rows;
    }

    public static WindowRow Describe(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero || !IsWindow(hwnd)) return null;
        uint owner;
        GetWindowThreadProcessId(hwnd, out owner);
        StringBuilder title = new StringBuilder(GetWindowTextLength(hwnd) + 2);
        GetWindowText(hwnd, title, title.Capacity);
        RECT rect;
        GetWindowRect(hwnd, out rect);
        WindowRow row = new WindowRow();
        row.Handle = hwnd.ToInt64(); row.Title = title.ToString(); row.Pid = (int)owner;
        row.Left = rect.Left; row.Top = rect.Top; row.Right = rect.Right; row.Bottom = rect.Bottom;
        return row;
    }

    public static bool Focus(IntPtr hwnd)
    {
        if (!IsWindow(hwnd)) return false;
        if (IsIconic(hwnd)) ShowWindow(hwnd, 9);
        if (SetForegroundWindow(hwnd)) return true;
        // A process that is not itself in the foreground may not raise a window
        // until it shares an input queue with the one that is.
        uint other;
        uint thread = GetWindowThreadProcessId(hwnd, out other);
        uint mine = GetCurrentThreadId();
        if (thread != mine && AttachThreadInput(mine, thread, true))
        {
            BringWindowToTop(hwnd);
            bool raised = SetForegroundWindow(hwnd);
            AttachThreadInput(mine, thread, false);
            if (raised) return true;
        }
        return GetForegroundWindow() == hwnd;
    }

    private static void Send(INPUT[] inputs)
    {
        uint sent = SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(INPUT)));
        if (sent != (uint)inputs.Length)
        {
            throw new InvalidOperationException("SendInput was blocked after " + sent + " of " + inputs.Length + " events");
        }
    }

    private static INPUT Mouse(uint flags, uint data, int x, int y, bool absolute)
    {
        INPUT input = new INPUT();
        input.type = INPUT_MOUSE;
        input.u.mi.dwFlags = flags | (absolute ? (MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK) : 0);
        input.u.mi.mouseData = data;
        if (absolute)
        {
            int left = GetSystemMetrics(76), top = GetSystemMetrics(77);
            int width = Math.Max(2, GetSystemMetrics(78)), height = Math.Max(2, GetSystemMetrics(79));
            input.u.mi.dx = (int)Math.Round((x - left) * 65535.0 / (width - 1));
            input.u.mi.dy = (int)Math.Round((y - top) * 65535.0 / (height - 1));
        }
        return input;
    }

    private static INPUT Key(ushort vk, ushort scan, uint flags)
    {
        INPUT input = new INPUT();
        input.type = INPUT_KEYBOARD;
        input.u.ki.wVk = vk; input.u.ki.wScan = scan; input.u.ki.dwFlags = flags;
        return input;
    }

    private static bool Extended(ushort vk)
    {
        return vk == 0x2E || vk == 0x2D || vk == 0x24 || vk == 0x23 || vk == 0x21 ||
               vk == 0x22 || vk == 0x25 || vk == 0x26 || vk == 0x27 || vk == 0x28 ||
               vk == 0x5B || vk == 0x5C;
    }

    public static void MouseMove(int x, int y)
    {
        Send(new INPUT[] { Mouse(MOUSEEVENTF_MOVE, 0, x, y, true) });
    }

    public static void MouseClick(int x, int y, string button, int count)
    {
        uint down = 0x0002, up = 0x0004;
        if (button == "right") { down = 0x0008; up = 0x0010; }
        else if (button == "middle") { down = 0x0020; up = 0x0040; }
        MouseMove(x, y);
        Thread.Sleep(16);
        for (int i = 0; i < Math.Max(1, count); i++)
        {
            Send(new INPUT[] { Mouse(down, 0, x, y, true), Mouse(up, 0, x, y, true) });
            if (i + 1 < count) Thread.Sleep(40);
        }
    }

    public static void MouseDrag(int x, int y, int toX, int toY, int steps, int ms)
    {
        steps = Math.Max(1, steps);
        MouseMove(x, y);
        Thread.Sleep(24);
        Send(new INPUT[] { Mouse(0x0002, 0, x, y, true) });
        int pause = Math.Max(1, ms / steps);
        for (int step = 1; step <= steps; step++)
        {
            MouseMove(x + (toX - x) * step / steps, y + (toY - y) * step / steps);
            Thread.Sleep(pause);
        }
        Send(new INPUT[] { Mouse(0x0004, 0, toX, toY, true) });
    }

    public static void Wheel(int x, int y, int delta)
    {
        MouseMove(x, y);
        Thread.Sleep(16);
        Send(new INPUT[] { Mouse(MOUSEEVENTF_WHEEL, unchecked((uint)(delta * 120)), x, y, true) });
    }

    public static void TypeText(string text)
    {
        foreach (char letter in text)
        {
            if (letter == '\r') continue;
            if (letter == '\n')
            {
                Send(new INPUT[] { Key(0x0D, 0, 0), Key(0x0D, 0, KEYEVENTF_KEYUP) });
                continue;
            }
            Send(new INPUT[] {
                Key(0, letter, KEYEVENTF_UNICODE),
                Key(0, letter, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
            });
        }
    }

    public static void KeyChord(ushort[] keys)
    {
        // A XAML app reads the scan code as well as the virtual key, so an
        // event without one is quietly ignored by exactly the windows this
        // host exists to drive.
        // One event per call with a beat between them: a modifier that arrives
        // in the same packet as the key it modifies is missed by some windows.
        foreach (ushort vk in keys)
        {
            Send(new INPUT[] { Key(vk, (ushort)MapVirtualKey(vk, 0), Extended(vk) ? KEYEVENTF_EXTENDEDKEY : 0) });
            Thread.Sleep(12);
        }
        for (int i = keys.Length - 1; i >= 0; i--)
        {
            uint flags = KEYEVENTF_KEYUP | (Extended(keys[i]) ? KEYEVENTF_EXTENDEDKEY : 0);
            Send(new INPUT[] { Key(keys[i], (ushort)MapVirtualKey(keys[i], 0), flags) });
            Thread.Sleep(12);
        }
    }
}
'@

# SetProcessDPIAware answers false when awareness was already set, so the
# state is read back rather than inferred from the call that asked for it.
$null = [MonkeyControlDesktop]::SetProcessDPIAware()
$script:DpiAware = [MonkeyControlDesktop]::IsProcessDPIAware()
$script:Elements = @{}
$script:Codes = @('HOST_ERROR', 'WINDOW_NOT_FOUND', 'TARGET_UNRESOLVED', 'BACKEND_UNAVAILABLE')
$script:Uia = [System.Windows.Automation.AutomationElement]
$script:Keys = @{
    'ctrl' = 0x11; 'control' = 0x11; 'alt' = 0x12; 'shift' = 0x10; 'win' = 0x5B
    'enter' = 0x0D; 'return' = 0x0D; 'tab' = 0x09; 'escape' = 0x1B; 'esc' = 0x1B
    'space' = 0x20; 'backspace' = 0x08; 'delete' = 0x2E; 'insert' = 0x2D
    'home' = 0x24; 'end' = 0x23; 'pageup' = 0x21; 'pagedown' = 0x22
    'up' = 0x26; 'down' = 0x28; 'left' = 0x25; 'right' = 0x27
}
for ($n = 1; $n -le 12; $n++) { $script:Keys["f$n"] = 0x6F + $n }

function Write-Line($payload) {
    [Console]::Out.WriteLine((ConvertTo-Json -InputObject $payload -Compress -Depth 12))
    [Console]::Out.Flush()
}

function Send-Result([int]$id, $result) {
    Write-Line ([ordered]@{ id = $id; ok = $true; result = $result })
}

function Send-Failure([int]$id, $record) {
    $message = [string]$record.Exception.Message
    $code = 'HOST_ERROR'
    $mark = $message.IndexOf(': ')
    if ($mark -gt 0 -and $script:Codes -contains $message.Substring(0, $mark)) {
        $code = $message.Substring(0, $mark)
        $message = $message.Substring($mark + 2)
    }
    Write-Line ([ordered]@{ id = $id; ok = $false; error = [ordered]@{ code = $code; message = $message } })
}

function Get-ProcessName([int]$processId) {
    try { return (Get-Process -Id $processId -ErrorAction Stop).ProcessName } catch { return '' }
}

function New-WindowPayload($row, [string]$matchedBy) {
    $payload = [ordered]@{
        handle = [int64]$row.Handle
        title = [string]$row.Title
        pid = [int]$row.Pid
        process = (Get-ProcessName $row.Pid)
        bounds = @([int]$row.Left, [int]$row.Top, [int]$row.Right, [int]$row.Bottom)
    }
    if ($matchedBy) { $payload['matched_by'] = $matchedBy }
    return $payload
}

function Convert-Rect($rect) {
    if ($null -eq $rect -or $rect.IsEmpty -or [double]::IsInfinity($rect.Left) -or [double]::IsNaN($rect.Left)) {
        return @(0, 0, 0, 0)
    }
    return @([int][Math]::Round($rect.Left), [int][Math]::Round($rect.Top),
             [int][Math]::Round($rect.Right), [int][Math]::Round($rect.Bottom))
}

function Get-ControlTypeName($element) {
    try { return ([string]$element.Current.ControlType.ProgrammaticName).Split('.')[-1] } catch { return 'Unknown' }
}

function Get-RuntimeId($element) {
    try { return [string]::Join('.', $element.GetRuntimeId()) } catch { return $null }
}

function Get-PatternNames($element) {
    # Every caller wraps this in @(): PowerShell unrolls the array on its way
    # out, so one pattern would otherwise reach JSON as a bare string and none
    # as nothing at all.
    $names = @()
    try {
        foreach ($pattern in $element.GetSupportedPatterns()) {
            $names += ([string]$pattern.ProgrammaticName).Split('.')[0] -replace 'PatternIdentifiers$', ''
        }
    } catch { }
    return $names
}

function New-ElementPayload($element, [string]$path) {
    $state = $element.Current
    $payload = [ordered]@{
        runtime_id = (Get-RuntimeId $element)
        controlType = (Get-ControlTypeName $element)
        name = [string]$state.Name
        automationId = [string]$state.AutomationId
        className = [string]$state.ClassName
        bounds = @(Convert-Rect $state.BoundingRectangle)
        enabled = [bool]$state.IsEnabled
        offscreen = [bool]$state.IsOffscreen
        patterns = @(Get-PatternNames $element)
    }
    if ($path) { $payload['path'] = $path }
    if ($payload['runtime_id']) { $script:Elements[$payload['runtime_id']] = $element }
    return $payload
}

function Get-Window([int64]$handle) {
    if ($handle -le 0) { throw 'WINDOW_NOT_FOUND: no window handle was given' }
    try { $element = $script:Uia::FromHandle([IntPtr]$handle) } catch { $element = $null }
    if ($null -eq $element) { throw "WINDOW_NOT_FOUND: window $handle is gone" }
    return $element
}

function Get-Element([string]$runtimeId, [int64]$handle) {
    if (-not $runtimeId) { throw 'TARGET_UNRESOLVED: no element was named' }
    $cached = $script:Elements[$runtimeId]
    if ($null -ne $cached) {
        try { $null = $cached.Current.IsEnabled; return $cached } catch { $script:Elements.Remove($runtimeId) }
    }
    if ($handle -gt 0) {
        $root = Get-Window $handle
        foreach ($found in $root.FindAll([System.Windows.Automation.TreeScope]::Subtree, [System.Windows.Automation.Condition]::TrueCondition)) {
            if ((Get-RuntimeId $found) -eq $runtimeId) { $script:Elements[$runtimeId] = $found; return $found }
        }
    }
    throw "TARGET_UNRESOLVED: element $runtimeId is no longer available; resolve it again"
}

function New-Condition($criteria) {
    $parts = New-Object System.Collections.ArrayList
    $exact = @{ name = $script:Uia::NameProperty; automation_id = $script:Uia::AutomationIdProperty; class_name = $script:Uia::ClassNameProperty }
    foreach ($key in @('name', 'automation_id', 'class_name')) {
        $value = $criteria.$key
        if ($value) { [void]$parts.Add((New-Object System.Windows.Automation.PropertyCondition($exact[$key], [string]$value))) }
    }
    if ($criteria.control_type) {
        $field = [System.Windows.Automation.ControlType].GetField([string]$criteria.control_type, [System.Reflection.BindingFlags]'Public,Static')
        if ($null -ne $field) {
            [void]$parts.Add((New-Object System.Windows.Automation.PropertyCondition($script:Uia::ControlTypeProperty, $field.GetValue($null))))
        }
    }
    if ($parts.Count -eq 0) { return [System.Windows.Automation.Condition]::TrueCondition }
    if ($parts.Count -eq 1) { return $parts[0] }
    return New-Object System.Windows.Automation.AndCondition($parts.ToArray([System.Windows.Automation.Condition]))
}

function Invoke-Find($payload) {
    $root = Get-Window ([int64]$payload.handle)
    $criteria = $payload.criteria
    if ($null -eq $criteria) { $criteria = [pscustomobject]@{} }
    $wanted = [string]$criteria.control_type
    $candidates = @()
    $truncated = $false
    foreach ($element in $root.FindAll([System.Windows.Automation.TreeScope]::Subtree, (New-Condition $criteria))) {
        if ($wanted -and (Get-ControlTypeName $element) -cne $wanted) { continue }
        if ($candidates.Count -ge 100) { $truncated = $true; break }
        $candidates += (New-ElementPayload $element '')
    }
    return [ordered]@{ candidates = @($candidates); truncated = $truncated }
}

function Invoke-Inspect($payload) {
    $root = Get-Window ([int64]$payload.handle)
    $depth = [int]$payload.depth; if ($depth -le 0) { $depth = 6 }
    $limit = [int]$payload.max_nodes; if ($limit -le 0) { $limit = 400 }
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $nodes = @()
    $queue = New-Object System.Collections.Queue
    $queue.Enqueue(@{ element = $root; path = '0'; level = 0 })
    $truncated = $false
    while ($queue.Count -gt 0) {
        if ($nodes.Count -ge $limit) { $truncated = $true; break }
        $entry = $queue.Dequeue()
        $nodes += (New-ElementPayload $entry.element $entry.path)
        if ($entry.level -ge $depth) { continue }
        $index = 0
        try { $child = $walker.GetFirstChild($entry.element) } catch { $child = $null }
        while ($null -ne $child) {
            $queue.Enqueue(@{ element = $child; path = "$($entry.path)/$index"; level = $entry.level + 1 })
            $index++
            try { $child = $walker.GetNextSibling($child) } catch { $child = $null }
        }
    }
    $rows = [MonkeyControlDesktop]::TopLevelWindows(0)
    $window = $null
    foreach ($row in $rows) { if ($row.Handle -eq [int64]$payload.handle) { $window = (New-WindowPayload $row '') } }
    return [ordered]@{ window = $window; nodes = @($nodes); truncated = $truncated }
}

function Get-ElementValue($element) {
    try {
        $pattern = $element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
        return [string]$pattern.Current.Value
    } catch { }
    try {
        # A rich document (Windows 11 Notepad) exposes its text, not a value.
        $pattern = $element.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
        return [string]$pattern.DocumentRange.GetText(4096)
    } catch { }
    return $null
}

function Invoke-Read($payload) {
    $element = Get-Element ([string]$payload.runtime_id) ([int64]$payload.handle)
    $state = $element.Current
    $toggled = $null
    try { $toggled = ($element.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Current.ToggleState -eq 'On') } catch { }
    return [ordered]@{
        value = (Get-ElementValue $element)
        enabled = [bool]$state.IsEnabled
        toggled = $toggled
        offscreen = [bool]$state.IsOffscreen
        bounds = @(Convert-Rect $state.BoundingRectangle)
    }
}

function Get-KeyCodes([string]$chord) {
    $codes = New-Object System.Collections.ArrayList
    foreach ($part in ($chord -split '\+')) {
        $key = $part.Trim().ToLowerInvariant()
        if (-not $key) { continue }
        if ($script:Keys.ContainsKey($key)) { [void]$codes.Add([uint16]$script:Keys[$key]); continue }
        if ($key.Length -eq 1 -and $key -match '^[a-z0-9]$') { [void]$codes.Add([uint16][char]$key.ToUpperInvariant()); continue }
        throw "HOST_ERROR: $part is not a key this host knows"
    }
    if ($codes.Count -eq 0) { throw 'HOST_ERROR: an empty chord presses nothing' }
    return [uint16[]]$codes.ToArray()
}

function Find-FamilyWindow([int]$processId, [bool]$deep) {
    $family = @($processId)
    if ($deep) {
        try {
            $rows = Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId -ErrorAction Stop
            $children = @($rows | Where-Object { $family -contains [int]$_.ParentProcessId } | ForEach-Object { [int]$_.ProcessId })
            $family += $children
            $family += @($rows | Where-Object { $children -contains [int]$_.ParentProcessId } | ForEach-Object { [int]$_.ProcessId })
        } catch { }
    }
    foreach ($member in ($family | Select-Object -Unique)) {
        $windows = [MonkeyControlDesktop]::TopLevelWindows($member)
        if ($windows.Count -gt 0) { return $windows[0] }
    }
    return $null
}

function Invoke-Launch($payload) {
    $command = @($payload.command)
    if ($command.Count -lt 1) { throw 'HOST_ERROR: launch needs a command' }
    $file = [string]$command[0]
    # Start-Process joins its argument list with spaces and quotes nothing,
    # so a path with a space has to arrive already quoted.
    $rest = @($command | Select-Object -Skip 1 | ForEach-Object {
        if ($_ -match '\s' -and $_ -notmatch '^".*"$') { '"' + $_ + '"' } else { $_ }
    })
    $waitMs = [int]$payload.wait_window_ms; if ($waitMs -le 0) { $waitMs = 10000 }
    if ($rest.Count -gt 0) { $started = Start-Process -FilePath $file -ArgumentList $rest -PassThru }
    else { $started = Start-Process -FilePath $file -PassThru }
    $basename = [System.IO.Path]::GetFileNameWithoutExtension($file)
    $deadline = [DateTime]::UtcNow.AddMilliseconds($waitMs)
    $round = 0
    while ([DateTime]::UtcNow -lt $deadline) {
        $row = Find-FamilyWindow $started.Id (($round % 4) -eq 3)
        if ($null -ne $row) { return (New-WindowPayload $row 'pid') }
        if ($started.HasExited) {
            # Explorer and the packaged Notepad hand their window to a process
            # that outlives the one that was started; the name is what is left.
            foreach ($other in (Get-Process -Name $basename -ErrorAction SilentlyContinue | Sort-Object StartTime -Descending)) {
                $windows = [MonkeyControlDesktop]::TopLevelWindows($other.Id)
                if ($windows.Count -gt 0) { return (New-WindowPayload $windows[0] 'process_name') }
            }
        }
        $round++
        Start-Sleep -Milliseconds 120
    }
    throw "WINDOW_NOT_FOUND: $file showed no window within ${waitMs}ms"
}

function Invoke-Op([string]$op, $payload) {
    if ($null -eq $payload) { $payload = [pscustomobject]@{} }
    switch ($op) {
        'hello' {
            return [ordered]@{
                host = 'execution'; version = '1'; dpi_aware = [bool]$script:DpiAware
                screen = @([MonkeyControlDesktop]::GetSystemMetrics(0), [MonkeyControlDesktop]::GetSystemMetrics(1))
            }
        }
        'windows' {
            $name = [string]$payload.process
            $ids = @()
            if ($name) {
                $ids = @(Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
                if ($ids.Count -eq 0) { return [ordered]@{ windows = @() } }
            } else { $ids = @(0) }
            $found = @()
            foreach ($id in $ids) {
                foreach ($row in [MonkeyControlDesktop]::TopLevelWindows($id)) {
                    if ($payload.title_regex -and ($row.Title -notmatch [string]$payload.title_regex)) { continue }
                    $found += (New-WindowPayload $row '')
                }
            }
            return [ordered]@{ windows = @($found) }
        }
        'foreground' {
            # Whatever holds input right now, titled or not: the runtime checks
            # this before it types, so an untitled window is an answer, never
            # a refusal.
            $row = [MonkeyControlDesktop]::Describe([MonkeyControlDesktop]::GetForegroundWindow())
            if ($null -eq $row) { throw 'WINDOW_NOT_FOUND: no window holds the foreground' }
            return (New-WindowPayload $row '')
        }
        'process' { return [ordered]@{ name = (Get-ProcessName ([int]$payload.pid)) } }
        'find' { return (Invoke-Find $payload) }
        'inspect' { return (Invoke-Inspect $payload) }
        'read' { return (Invoke-Read $payload) }
        'focus' {
            $raised = [MonkeyControlDesktop]::Focus([IntPtr][int64]$payload.handle)
            # Null when no element was named; otherwise whether it took focus,
            # which the caller needs because the next keystroke depends on it.
            $focused = $null
            if ($payload.runtime_id) {
                $focused = $false
                try {
                    (Get-Element ([string]$payload.runtime_id) ([int64]$payload.handle)).SetFocus()
                    $focused = $true
                } catch {
                    [Console]::Error.WriteLine("SetFocus refused: $($_.Exception.Message)")
                }
            }
            return [ordered]@{ foreground = [bool]$raised; element_focused = $focused }
        }
        'invoke' {
            $element = Get-Element ([string]$payload.runtime_id) ([int64]$payload.handle)
            try { $pattern = $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern) } catch { $pattern = $null }
            if ($null -eq $pattern) { return [ordered]@{ invoked = $false } }
            $pattern.Invoke()
            return [ordered]@{ invoked = $true }
        }
        'set_value' {
            $element = Get-Element ([string]$payload.runtime_id) ([int64]$payload.handle)
            try { $pattern = $element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern) } catch { $pattern = $null }
            if ($null -eq $pattern -or $pattern.Current.IsReadOnly) { return [ordered]@{ set = $false } }
            $pattern.SetValue([string]$payload.text)
            return [ordered]@{ set = $true }
        }
        'click' {
            $count = [int]$payload.count; if ($count -le 0) { $count = 1 }
            $button = [string]$payload.button; if (-not $button) { $button = 'left' }
            [MonkeyControlDesktop]::MouseClick([int]$payload.x, [int]$payload.y, $button, $count)
            return [ordered]@{}
        }
        'move' { [MonkeyControlDesktop]::MouseMove([int]$payload.x, [int]$payload.y); return [ordered]@{} }
        'drag' {
            [MonkeyControlDesktop]::MouseDrag([int]$payload.x, [int]$payload.y, [int]$payload.to_x, [int]$payload.to_y, [int]$payload.steps, [int]$payload.ms)
            return [ordered]@{}
        }
        'type' { [MonkeyControlDesktop]::TypeText([string]$payload.text); return [ordered]@{} }
        'keys' { [MonkeyControlDesktop]::KeyChord((Get-KeyCodes ([string]$payload.chord))); return [ordered]@{} }
        'scroll' { [MonkeyControlDesktop]::Wheel([int]$payload.x, [int]$payload.y, [int]$payload.delta); return [ordered]@{} }
        'launch' { return (Invoke-Launch $payload) }
        default { throw "HOST_ERROR: $op is not an execution host op" }
    }
}

[Console]::Error.WriteLine('monkeycontrol execution host ready')
while ($true) {
    $line = $script:StdIn.ReadLine()
    if ($null -eq $line) { break }
    $line = $line.Trim().TrimStart([char]0xFEFF)
    if (-not $line) { continue }
    $id = 0
    try {
        $request = ConvertFrom-Json $line
        $id = [int]$request.id
        if ([string]$request.op -eq 'exit') { break }
        Send-Result $id (Invoke-Op ([string]$request.op) $request.args)
    } catch {
        Send-Failure $id $_
    }
}
