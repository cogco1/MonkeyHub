# MonkeyControl presentation host: the demo overlay and screen capture, STA.
#
# Speaks the same JSON lines as the execution host. The overlay is a WPF window
# with no chrome, always on top, never activated and click-through, so showing
# a target can never change what the next click reaches. A capture leaves this
# process as base64 PNG bytes: this host writes no file either.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
# Console input decodes with the OEM code page unless it is told not to,
# which would turn every non-ASCII element name and typed character into
# mojibake before the first op ever runs.
$script:StdIn = New-Object System.IO.StreamReader(
    [Console]::OpenStandardInput(), (New-Object System.Text.UTF8Encoding($false)))

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
Add-Type -AssemblyName System.Drawing

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class MonkeyControlOverlay
{
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    public struct POINT { public int X; public int Y; }

    [StructLayout(LayoutKind.Sequential)]
    public struct MONITORINFO
    {
        public int cbSize; public RECT rcMonitor; public RECT rcWork; public uint dwFlags;
    }

    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool IsProcessDPIAware();
    [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")] private static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint flags);
    [DllImport("user32.dll")] private static extern IntPtr MonitorFromPoint(POINT point, uint flags);
    [DllImport("user32.dll")] private static extern bool GetMonitorInfo(IntPtr monitor, ref MONITORINFO info);

    private const uint MONITOR_DEFAULTTOPRIMARY = 1;
    private const uint MONITORINFOF_PRIMARY = 1;

    // The screen one window, or one point, actually lives on: a recording that
    // followed the whole virtual desktop would keep every unrelated window on
    // the other monitor, and a badge placed on the virtual screen's centre
    // would explain a target the viewer is not looking at.
    private static int[] Describe(IntPtr monitor)
    {
        MONITORINFO info = new MONITORINFO();
        info.cbSize = Marshal.SizeOf(typeof(MONITORINFO));
        if (!GetMonitorInfo(monitor, ref info))
        {
            return new int[] { 0, 0, GetSystemMetrics(0), GetSystemMetrics(1), 1 };
        }
        return new int[] {
            info.rcMonitor.Left, info.rcMonitor.Top, info.rcMonitor.Right, info.rcMonitor.Bottom,
            (info.dwFlags & MONITORINFOF_PRIMARY) != 0 ? 1 : 0
        };
    }

    public static int[] MonitorOfWindow(IntPtr hwnd)
    {
        return Describe(MonitorFromWindow(hwnd, MONITOR_DEFAULTTOPRIMARY));
    }

    public static int[] MonitorOfPoint(int x, int y)
    {
        POINT point = new POINT(); point.X = x; point.Y = y;
        return Describe(MonitorFromPoint(point, MONITOR_DEFAULTTOPRIMARY));
    }
    [DllImport("user32.dll", SetLastError = true)] public static extern int GetWindowLong(IntPtr hwnd, int index);
    [DllImport("user32.dll", SetLastError = true)] public static extern int SetWindowLong(IntPtr hwnd, int index, int value);
    [DllImport("user32.dll", SetLastError = true)] public static extern bool SetWindowDisplayAffinity(IntPtr hwnd, uint affinity);

    public const int GWL_EXSTYLE = -20;
    // Transparent lets every click through, layered makes that reliable,
    // toolwindow keeps the overlay out of Alt-Tab, noactivate out of focus.
    public const int CLICK_THROUGH = 0x00000020 | 0x00080000 | 0x00000080 | 0x08000000;
    // Windows 10 2004 and later leave this window out of a capture entirely,
    // showing what is behind it rather than a black rectangle.
    public const uint WDA_EXCLUDEFROMCAPTURE = 0x00000011;

    public static void MakeClickThrough(IntPtr hwnd)
    {
        SetWindowLong(hwnd, GWL_EXSTYLE, GetWindowLong(hwnd, GWL_EXSTYLE) | CLICK_THROUGH);
    }

    // A recording has to show the desktop, not the explanation drawn over it:
    // the raw frames stay replayable under any overlay projection, or none.
    public static bool ExcludeFromCapture(IntPtr hwnd)
    {
        return SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE);
    }
}
'@

# SetProcessDPIAware answers false when awareness was already set, so the
# state is read back rather than inferred from the call that asked for it.
$null = [MonkeyControlOverlay]::SetProcessDPIAware()
$script:DpiAware = [MonkeyControlOverlay]::IsProcessDPIAware()
$script:Codes = @('HOST_ERROR', 'BACKEND_UNAVAILABLE')
$script:Overlay = $null
$script:Canvas = $null
# Whether this desktop honoured the request to keep the overlay out of
# captures; null until the overlay window exists.
$script:Excluded = $null
# Where the last badge was drawn, in physical pixels.
$script:LastBadge = $null
$script:ScaleX = 1.0
$script:ScaleY = 1.0
$script:BadgeColors = @{ info = '#2D6CDF'; ok = '#1F9D55'; fail = '#C62828' }

function Write-Line($payload) {
    [Console]::Out.WriteLine((ConvertTo-Json -InputObject $payload -Compress -Depth 8))
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

function Get-VirtualScreen() {
    return @{
        left = [MonkeyControlOverlay]::GetSystemMetrics(76)
        top = [MonkeyControlOverlay]::GetSystemMetrics(77)
        width = [MonkeyControlOverlay]::GetSystemMetrics(78)
        height = [MonkeyControlOverlay]::GetSystemMetrics(79)
    }
}

function Get-Brush([string]$value, [string]$fallback) {
    if (-not $value) { $value = $fallback }
    try { $color = [System.Windows.Media.ColorConverter]::ConvertFromString($value) }
    catch { $color = [System.Windows.Media.ColorConverter]::ConvertFromString($fallback) }
    $brush = New-Object System.Windows.Media.SolidColorBrush($color)
    $brush.Freeze()
    return $brush
}

function Invoke-Pump([int]$ms) {
    # The overlay lives on this thread, so the loop that reads stdin is also the
    # one that must let WPF render: every op ends by pumping its own dispatcher.
    $dispatcher = [System.Windows.Threading.Dispatcher]::CurrentDispatcher
    $deadline = [DateTime]::UtcNow.AddMilliseconds([Math]::Max(0, $ms))
    while ($true) {
        # Assigned away: a stray pipeline value here would become part of the
        # reply of whichever op pumped last.
        $null = $dispatcher.Invoke([System.Windows.Threading.DispatcherPriority]::Background, [action] { })
        if ([DateTime]::UtcNow -ge $deadline) { break }
        Start-Sleep -Milliseconds 15
    }
}

function Get-Overlay() {
    if ($null -ne $script:Overlay) { return $script:Overlay }
    $screen = Get-VirtualScreen
    $window = New-Object System.Windows.Window
    $window.WindowStyle = [System.Windows.WindowStyle]::None
    $window.AllowsTransparency = $true
    $window.Background = [System.Windows.Media.Brushes]::Transparent
    $window.Topmost = $true
    $window.ShowInTaskbar = $false
    $window.ShowActivated = $false
    $window.ResizeMode = [System.Windows.ResizeMode]::NoResize
    $window.WindowStartupLocation = [System.Windows.WindowStartupLocation]::Manual
    $window.Title = 'MonkeyControl overlay'
    $window.Left = $screen.left
    $window.Top = $screen.top
    $window.Width = $screen.width
    $window.Height = $screen.height
    $canvas = New-Object System.Windows.Controls.Canvas
    $canvas.IsHitTestVisible = $false
    $window.Content = $canvas
    $window.Show()
    $handle = (New-Object System.Windows.Interop.WindowInteropHelper($window)).Handle
    [MonkeyControlOverlay]::MakeClickThrough($handle)
    $script:Excluded = [MonkeyControlOverlay]::ExcludeFromCapture($handle)
    if (-not $script:Excluded) {
        [Console]::Error.WriteLine('SetWindowDisplayAffinity refused: the overlay may appear in captures')
    }
    $source = [System.Windows.PresentationSource]::FromVisual($window)
    if ($null -ne $source) {
        $script:ScaleX = [double]$source.CompositionTarget.TransformToDevice.M11
        $script:ScaleY = [double]$source.CompositionTarget.TransformToDevice.M22
    }
    if ($script:ScaleX -le 0) { $script:ScaleX = 1.0 }
    if ($script:ScaleY -le 0) { $script:ScaleY = 1.0 }
    # The window was placed in physical pixels; WPF wants device-independent
    # units, so the same rectangle is restated once the scale is known.
    $window.Left = $screen.left / $script:ScaleX
    $window.Top = $screen.top / $script:ScaleY
    $window.Width = $screen.width / $script:ScaleX
    $window.Height = $screen.height / $script:ScaleY
    $script:Overlay = $window
    $script:Canvas = $canvas
    Invoke-Pump 0
    return $window
}

function New-Chip([string]$text, $background, [double]$size) {
    $label = New-Object System.Windows.Controls.TextBlock
    $label.Text = $text
    $label.Foreground = [System.Windows.Media.Brushes]::White
    $label.FontFamily = New-Object System.Windows.Media.FontFamily('Segoe UI')
    $label.FontSize = $size
    $label.FontWeight = [System.Windows.FontWeights]::SemiBold
    $chip = New-Object System.Windows.Controls.Border
    $chip.Background = $background
    $chip.CornerRadius = New-Object System.Windows.CornerRadius(6)
    $chip.Padding = New-Object System.Windows.Thickness(10, 4, 10, 5)
    $chip.Child = $label
    return $chip
}

function Show-Highlight($payload) {
    $window = Get-Overlay
    $bounds = @($payload.bounds)
    if ($bounds.Count -ne 4) { throw 'HOST_ERROR: highlight needs [left, top, right, bottom]' }
    $screen = Get-VirtualScreen
    $left = ([double]$bounds[0] - $screen.left) / $script:ScaleX
    $top = ([double]$bounds[1] - $screen.top) / $script:ScaleY
    $width = [Math]::Max(2.0, ([double]$bounds[2] - [double]$bounds[0]) / $script:ScaleX)
    $height = [Math]::Max(2.0, ([double]$bounds[3] - [double]$bounds[1]) / $script:ScaleY)
    $brush = Get-Brush ([string]$payload.color) '#FF7A00'
    $script:Canvas.Children.Clear()
    $outline = New-Object System.Windows.Shapes.Rectangle
    $outline.Stroke = $brush
    $outline.StrokeThickness = 3
    $outline.RadiusX = 3
    $outline.RadiusY = 3
    $outline.Width = $width
    $outline.Height = $height
    [System.Windows.Controls.Canvas]::SetLeft($outline, $left)
    [System.Windows.Controls.Canvas]::SetTop($outline, $top)
    [void]$script:Canvas.Children.Add($outline)
    $kind = [string]$payload.kind
    $caption = [string]$payload.label
    if ($kind) { $caption = "$kind - $caption" }
    $chip = New-Chip $caption $brush 13
    [System.Windows.Controls.Canvas]::SetLeft($chip, $left)
    [System.Windows.Controls.Canvas]::SetTop($chip, [Math]::Max(0.0, $top - 28))
    [void]$script:Canvas.Children.Add($chip)
    $window.Show()
    Complete-Overlay ([int]$payload.ms)
}

function Get-Monitor($payload) {
    $handle = 0
    if ($null -ne $payload.handle) { $handle = [int64]$payload.handle }
    if ($handle -gt 0) { $found = [MonkeyControlOverlay]::MonitorOfWindow([IntPtr]$handle) }
    else { $found = [MonkeyControlOverlay]::MonitorOfPoint(0, 0) }
    return [ordered]@{
        bounds = @([int]$found[0], [int]$found[1], [int]$found[2], [int]$found[3])
        primary = ([int]$found[4] -eq 1)
    }
}

function Show-Badge($payload) {
    $window = Get-Overlay
    $kind = [string]$payload.kind
    if (-not $script:BadgeColors.ContainsKey($kind)) { $kind = 'info' }
    $screen = Get-VirtualScreen
    $script:Canvas.Children.Clear()
    $chip = New-Chip ([string]$payload.text) (Get-Brush $script:BadgeColors[$kind] '#2D6CDF') 18
    $chip.Measure((New-Object System.Windows.Size([double]::PositiveInfinity, [double]::PositiveInfinity)))
    $width = $chip.DesiredSize.Width
    $height = $chip.DesiredSize.Height
    $anchor = @($payload.anchor)
    if ($anchor.Count -eq 4) {
        # A verdict belongs next to what it is about: the chip sits under the
        # rectangle just acted on, kept inside the monitor that rectangle is on
        # rather than the virtual desktop, which may be another screen away.
        $middle = ([double]$anchor[0] + [double]$anchor[2]) / 2
        $box = [MonkeyControlOverlay]::MonitorOfPoint([int]$middle, [int]$anchor[3])
        $left = ($middle / $script:ScaleX) - ($width / 2)
        $top = ([double]$anchor[3] / $script:ScaleY) + 14
        $edge = [double]$box[3] / $script:ScaleY
        if (($top + $height) -gt $edge) { $top = ([double]$anchor[1] / $script:ScaleY) - $height - 14 }
        $left = [Math]::Max([double]$box[0] / $script:ScaleX, [Math]::Min($left, ([double]$box[2] / $script:ScaleX) - $width))
        $top = [Math]::Max([double]$box[1] / $script:ScaleY, $top)
        [System.Windows.Controls.Canvas]::SetLeft($chip, $left - ($screen.left / $script:ScaleX))
        [System.Windows.Controls.Canvas]::SetTop($chip, $top - ($screen.top / $script:ScaleY))
    } else {
        [System.Windows.Controls.Canvas]::SetLeft($chip, [Math]::Max(0.0, ($screen.width / $script:ScaleX - $width) / 2))
        [System.Windows.Controls.Canvas]::SetTop($chip, 48)
    }
    # Where the chip actually landed, in physical pixels: the overlay is
    # excluded from every capture, so this reply is the only way a caller can
    # check that a verdict was said next to the thing it is about.
    $script:LastBadge = @(
        [int](([double][System.Windows.Controls.Canvas]::GetLeft($chip) + ($screen.left / $script:ScaleX)) * $script:ScaleX),
        [int](([double][System.Windows.Controls.Canvas]::GetTop($chip) + ($screen.top / $script:ScaleY)) * $script:ScaleY)
    )
    [void]$script:Canvas.Children.Add($chip)
    $window.Show()
    Complete-Overlay ([int]$payload.ms)
}

function Complete-Overlay([int]$ms) {
    # A positive duration is a beat the caller asked for: show it, then take it
    # down. Zero or less leaves it up until clear, which is how a recording
    # keeps one target marked across several frames.
    if ($ms -gt 0) {
        Invoke-Pump $ms
        Clear-Overlay
    } else {
        Invoke-Pump 0
    }
}

function Clear-Overlay() {
    if ($null -ne $script:Overlay) {
        $script:Canvas.Children.Clear()
        $script:Overlay.Hide()
        Invoke-Pump 0
    }
}

function Invoke-Screenshot($payload) {
    $screen = Get-VirtualScreen
    $bounds = $payload.bounds
    if ($null -eq $bounds) {
        $left = $screen.left; $top = $screen.top
        $width = $screen.width; $height = $screen.height
    } else {
        $box = @($bounds)
        if ($box.Count -ne 4) { throw 'HOST_ERROR: a screenshot region needs [left, top, right, bottom]' }
        $left = [int]$box[0]; $top = [int]$box[1]
        $width = [int]$box[2] - $left; $height = [int]$box[3] - $top
    }
    if ($width -le 0 -or $height -le 0) { throw 'HOST_ERROR: a screenshot needs a positive region' }
    $bitmap = New-Object System.Drawing.Bitmap($width, $height, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $stream = New-Object System.IO.MemoryStream
    try {
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.CopyFromScreen($left, $top, 0, 0,
                (New-Object System.Drawing.Size($width, $height)),
                [System.Drawing.CopyPixelOperation]::SourceCopy)
        } finally { $graphics.Dispose() }
        $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
        $data = $stream.ToArray()
    } finally {
        $stream.Dispose()
        $bitmap.Dispose()
    }
    $hash = [System.Security.Cryptography.SHA256]::Create()
    try { $digest = -join ($hash.ComputeHash($data) | ForEach-Object { $_.ToString('x2') }) }
    finally { $hash.Dispose() }
    return [ordered]@{
        png_base64 = [Convert]::ToBase64String($data)
        sha256 = $digest
        bounds = @($left, $top, ($left + $width), ($top + $height))
        bytes = $data.Length
    }
}

function Invoke-Op([string]$op, $payload) {
    if ($null -eq $payload) { $payload = [pscustomobject]@{} }
    switch ($op) {
        'hello' {
            $screen = Get-VirtualScreen
            return [ordered]@{
                host = 'presentation'; version = '1'; dpi_aware = [bool]$script:DpiAware
                screen = @([MonkeyControlOverlay]::GetSystemMetrics(0), [MonkeyControlOverlay]::GetSystemMetrics(1))
                virtual_screen = @($screen.left, $screen.top, ($screen.left + $screen.width), ($screen.top + $screen.height))
            }
        }
        'screenshot' { return (Invoke-Screenshot $payload) }
        'highlight' { Show-Highlight $payload; return [ordered]@{ excluded_from_capture = $script:Excluded } }
        'badge' {
            Show-Badge $payload
            return [ordered]@{ excluded_from_capture = $script:Excluded; placed = $script:LastBadge }
        }
        'clear' { Clear-Overlay; return [ordered]@{} }
        'monitor' { return (Get-Monitor $payload) }
        default { throw "HOST_ERROR: $op is not a presentation host op" }
    }
}

[Console]::Error.WriteLine('monkeycontrol presentation host ready')
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
if ($null -ne $script:Overlay) { $script:Overlay.Close(); Invoke-Pump 0 }
