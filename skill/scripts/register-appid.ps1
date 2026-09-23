# Registers the identity that notifications are sent under.
# ASCII only on purpose (see _env.ps1).
#
# A toast is delivered under an application identity, and Windows drops it
# silently when that identity has no Start Menu shortcut carrying the matching
# System.AppUserModel.ID. Borrowing the built-in Windows PowerShell identity
# therefore works only on machines that still have its shortcut - on the others
# the report is written and the notification never appears.
# So the tool registers an identity of its own, through the shell API, because a
# normal shortcut has no place to put that property.

param(
    [string]$AppId = 'WorkReport.Notify',
    [string]$DisplayName = 'work-report',
    [string]$Target,          # what the Start Menu entry runs
    [string]$Arguments,
    [string]$Icon             # .ico shown in the Start Menu and on notifications
)

$ErrorActionPreference = 'Stop'

if (-not $Target) { $Target = Join-Path $PSHOME 'powershell.exe' }

if (-not ('WorkReport.Shortcut' -as [type])) {
    Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;

namespace WorkReport {
    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    public struct PropertyKey {
        public Guid fmtid; public uint pid;
        public PropertyKey(Guid g, uint p) { fmtid = g; pid = p; }
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct PropVariant {
        public ushort vt; ushort r1; ushort r2; ushort r3; public IntPtr p; int p2;
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPropertyStore {
        void GetCount(out uint c);
        void GetAt(uint i, out PropertyKey k);
        void GetValue(ref PropertyKey k, out PropVariant v);
        void SetValue(ref PropertyKey k, ref PropVariant v);
        void Commit();
    }

    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IShellLinkW {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder f, int c, IntPtr fd, uint fl);
        void GetIDList(out IntPtr ppidl);
        void SetIDList(IntPtr pidl);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder n, int c);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string n);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder d, int c);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string d);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder a, int c);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string a);
        void GetHotkey(out short h);
        void SetHotkey(short h);
        void GetShowCmd(out int c);
        void SetShowCmd(int c);
        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder i, int c, out int idx);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string i, int idx);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string p, uint r);
        void Resolve(IntPtr hwnd, uint fl);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string p);
    }

    [ComImport, Guid("0000010b-0000-0000-C000-000000000046"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPersistFile {
        void GetClassID(out Guid c);
        [PreserveSig] int IsDirty();
        void Load([MarshalAs(UnmanagedType.LPWStr)] string f, uint mode);
        void Save([MarshalAs(UnmanagedType.LPWStr)] string f, [MarshalAs(UnmanagedType.Bool)] bool remember);
        void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string f);
        void GetCurFile([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder f);
    }

    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    public class CShellLink { }

    public static class Shortcut {
        const ushort VT_LPWSTR = 31;

        // propsys.dll exports no helper for this: InitPropVariantFromString is an
        // inline header function, so the variant is built by hand.
        static PropVariant StringVariant(string value) {
            PropVariant pv = new PropVariant();
            pv.vt = VT_LPWSTR;
            pv.p = Marshal.StringToCoTaskMemUni(value);
            return pv;
        }

        public static void Create(string linkPath, string target, string args,
                                  string appId, string description, string icon) {
            IShellLinkW link = (IShellLinkW)new CShellLink();
            link.SetPath(target);
            if (!string.IsNullOrEmpty(args)) link.SetArguments(args);
            if (!string.IsNullOrEmpty(description)) link.SetDescription(description);
            if (!string.IsNullOrEmpty(icon)) link.SetIconLocation(icon, 0);

            IPropertyStore store = (IPropertyStore)link;
            // System.AppUserModel.ID
            PropertyKey key = new PropertyKey(
                new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5);
            PropVariant pv = StringVariant(appId);
            store.SetValue(ref key, ref pv);   // the store copies the value
            store.Commit();
            Marshal.FreeCoTaskMem(pv.p);

            ((IPersistFile)link).Save(linkPath, true);
            Marshal.ReleaseComObject(link);
        }
    }
}
'@
}

$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
if (-not (Test-Path $startMenu)) { New-Item -ItemType Directory -Path $startMenu -Force | Out-Null }
$linkPath = Join-Path $startMenu "$DisplayName.lnk"

[WorkReport.Shortcut]::Create($linkPath, $Target, $Arguments, $AppId, 'work-report', $Icon)

# The registry entry is what gives the identity a name in notification settings.
$appKey = "HKCU:\Software\Classes\AppUserModelId\$AppId"
New-Item -Path $appKey -Force | Out-Null
Set-ItemProperty -Path $appKey -Name 'DisplayName' -Value $DisplayName
Set-ItemProperty -Path $appKey -Name 'ShowInSettings' -Value 1 -Type DWord

Write-Output $linkPath
