# Desktop notification helpers.

function Get-Messages {
    # Notification text lives in a UTF-8 file so the scripts can stay ASCII:
    # a BOM-less script with non-ASCII text is decoded with the machine's code
    # page and breaks on someone else's computer.
    $path = Join-Path $PSScriptRoot 'messages.json'
    try { return Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { return $null }
}

function Hide-Toast {
    # Clears an earlier toast so a finished run does not leave "in progress"
    # sitting in the notification centre.
    param([string]$Tag = 'work-report', [string]$Group = 'work-report')
    foreach ($appId in @('WorkReport.Notify',
            '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe')) {
        try {
            [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
            [Windows.UI.Notifications.ToastNotificationManager]::History.Remove($Tag, $Group, $appId)
        }
        catch { }
    }
}

function Show-Toast {
    <#
      Windows notification with no external module.
      Layered on purpose: the toast API is not available in every host, so a
      failure to notify must never fail the run. Each layer is tried in turn
      and the last one just returns.
        1. WinRT toast      - Windows 10/11 under Windows PowerShell
        2. tray balloon     - any Windows with WinForms
        3. nothing          - headless or blocked; the run log still records it
      Same Tag/Group on purpose: the finished toast replaces the running one.
    #>
    param(
        [Parameter(Mandatory)][string]$Title,
        [string]$Message = '',
        [ValidateSet('info', 'error')][string]$Kind = 'info',
        [string]$Tag = 'work-report',
        [string]$OpenPath           # clicking the toast opens this
    )

    if ($env:WORK_REPORT_NO_TOAST -eq '1') { return 'disabled' }

    # Windows refuses file:// from a toast, so the click carries a private
    # scheme that install.ps1 registers. The path is NOT percent-encoded and the
    # scheme runs a .cmd wrapper: a toast click does not reach a handler that
    # points straight at powershell.exe, and does not survive an encoded path.
    function ToUri([string]$p) { if ($p) { "workreport://$p" } else { '' } }
    function Esc([string]$s) { [System.Security.SecurityElement]::Escape($s) }

    try {
        [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
        [void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime]

        $body = '<toast activationType="protocol" launch="{0}"><visual><binding template="ToastText02"><text id="1">{1}</text><text id="2">{2}</text></binding></visual></toast>' -f `
        (Esc (ToUri $OpenPath)), (Esc $Title), (Esc $Message)

        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml($body)
        $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
        $toast.Tag = $Tag
        $toast.Group = 'work-report'
        # Our own AppId, registered by install.ps1. Windows delivers a toast
        # under an application identity and drops it silently when that identity
        # has no Start Menu shortcut carrying the matching AppUserModelID, so
        # borrowing the built-in PowerShell identity fails on any machine whose
        # shortcut is gone - report written, notification never seen. That one
        # stays only as a fallback.
        foreach ($appId in @('WorkReport.Notify',
                '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe')) {
            try {
                [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
                return 'toast'
            }
            catch { }
        }
        return 'none'
    }
    catch { }

    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        Add-Type -AssemblyName System.Drawing -ErrorAction Stop
        $icon = New-Object System.Windows.Forms.NotifyIcon
        $icon.Icon = if ($Kind -eq 'error') { [System.Drawing.SystemIcons]::Error }
        else { [System.Drawing.SystemIcons]::Information }
        $icon.Visible = $true
        $icon.ShowBalloonTip(5000, $Title, $Message, $(if ($Kind -eq 'error') { 'Error' } else { 'Info' }))
        Start-Sleep -Seconds 5          # the balloon dies with the icon
        $icon.Dispose()
        return 'balloon'
    }
    catch { }

    return 'none'
}

