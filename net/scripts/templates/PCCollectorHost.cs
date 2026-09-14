using System;
using System.IO;
using System.Reflection;
using System.Diagnostics;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;

internal static class PCCollectorHost {
    static readonly byte[] Magic = Encoding.ASCII.GetBytes("PCCOLV01");

    static void Protect(string directory) {
        var acl = new DirectorySecurity();
        acl.SetAccessRuleProtection(true, false);
        foreach (var sid in new[] { WindowsIdentity.GetCurrent().User, new SecurityIdentifier("S-1-5-18"), new SecurityIdentifier("S-1-5-32-544") })
            acl.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.FullControl, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
        new DirectoryInfo(directory).SetAccessControl(acl);
    }
    static byte[] ReadExactly(Stream input, int length) {
        var bytes = new byte[length]; int offset = 0;
        while (offset < length) { int count = input.Read(bytes, offset, length - offset); if (count == 0) throw new InvalidDataException("Truncated collector package."); offset += count; }
        return bytes;
    }
    static bool Equal(byte[] left, byte[] right) {
        if (left.Length != right.Length) return false;
        int different = 0; for (int i = 0; i < left.Length; i++) different |= left[i] ^ right[i];
        return different == 0;
    }
    static void ExtractPayload(string script, string library) {
        string executable = Assembly.GetExecutingAssembly().Location;
        using (var file = new FileStream(executable, FileMode.Open, FileAccess.Read, FileShare.Read)) {
            if (file.Length < 48) throw new InvalidDataException("Collector package is missing.");
            file.Position = file.Length - 48;
            byte[] footer = ReadExactly(file, 48);
            for (int i = 0; i < Magic.Length; i++) if (footer[i] != Magic[i]) throw new InvalidDataException("Collector package marker is invalid.");
            long payloadLength = BitConverter.ToInt64(footer, 8);
            if (payloadLength < 80 || payloadLength > file.Length - 48 || payloadLength > Int32.MaxValue) throw new InvalidDataException("Collector package length is invalid.");
            file.Position = file.Length - 48 - payloadLength;
            byte[] payload = ReadExactly(file, (int)payloadLength);
            using (var hash = SHA256.Create()) if (!Equal(hash.ComputeHash(payload), ReadPart(footer, 16, 32))) throw new InvalidDataException("Collector package checksum failed.");
            long scriptLength = BitConverter.ToInt64(payload, 0), libraryLength = BitConverter.ToInt64(payload, 8);
            if (scriptLength < 1 || libraryLength < 1 || scriptLength > Int32.MaxValue || libraryLength > Int32.MaxValue || 80 + scriptLength + libraryLength != payloadLength) throw new InvalidDataException("Collector package content is invalid.");
            byte[] scriptBytes = ReadPart(payload, 80, (int)scriptLength);
            byte[] libraryBytes = ReadPart(payload, 80 + (int)scriptLength, (int)libraryLength);
            using (var hash = SHA256.Create()) {
                if (!Equal(hash.ComputeHash(scriptBytes), ReadPart(payload, 16, 32)) || !Equal(hash.ComputeHash(libraryBytes), ReadPart(payload, 48, 32))) throw new InvalidDataException("Collector resource checksum failed.");
            }
            File.WriteAllBytes(script, scriptBytes); File.WriteAllBytes(library, libraryBytes);
        }
    }
    static byte[] ReadPart(byte[] source, int offset, int length) { var part = new byte[length]; Buffer.BlockCopy(source, offset, part, 0, length); return part; }
    static void Log(string text) {
        string root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PCDailyCollector");
        Directory.CreateDirectory(root); string path = Path.Combine(root, "collector.log");
        if (File.Exists(path) && new FileInfo(path).Length > 1048576) { File.Copy(path, path + ".previous", true); File.WriteAllText(path, ""); }
        File.AppendAllText(path, DateTime.UtcNow.ToString("o") + " " + text + Environment.NewLine, Encoding.UTF8);
    }
    public static int Main(string[] args) {
        bool preview = args.Length == 1 && args[0].Equals("-Preview", StringComparison.OrdinalIgnoreCase);
        bool selfTest = args.Length == 1 && args[0] == "--self-test";
        if (args.Length != 0 && !preview && !selfTest) { Console.Error.WriteLine("Usage: PCCollector.exe [-Preview | --self-test]"); return 2; }
        string root = Path.Combine(Path.GetTempPath(), "PCCollector-" + Guid.NewGuid().ToString("N"));
        string script = Path.Combine(root, "GetInfo_Upload.ps1"), library = Path.Combine(root, "OpenHardwareMonitorLib.dll");
        try {
            Directory.CreateDirectory(root); Protect(root); ExtractPayload(script, library);
            string windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
            string system = Environment.Is64BitOperatingSystem && !Environment.Is64BitProcess ? "Sysnative" : "System32";
            var info = new ProcessStartInfo { FileName = Path.Combine(windows, system, @"WindowsPowerShell\v1.0\powershell.exe"), Arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" + script + "\"" + (preview ? " -Preview" : selfTest ? " -PackageSelfTest" : ""), UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = root, RedirectStandardOutput = true, RedirectStandardError = true, StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
            info.EnvironmentVariables["PSModulePath"] = Path.Combine(windows, @"System32\WindowsPowerShell\v1.0\Modules");
            using (var process = Process.Start(info)) {
                var stdout = process.StandardOutput.ReadToEndAsync(); var stderr = process.StandardError.ReadToEndAsync();
                if (!process.WaitForExit(15 * 60 * 1000)) { process.Kill(); process.WaitForExit(); throw new TimeoutException("Collector exceeded 15 minutes."); }
                string output = stdout.Result, error = stderr.Result; Console.Write(output); Console.Error.Write(error);
                if (!selfTest && !preview) Log("exit=" + process.ExitCode + (String.IsNullOrWhiteSpace(error) ? "" : " " + error)); return process.ExitCode;
            }
        } catch (Exception ex) { Console.Error.WriteLine(ex.Message); if (!selfTest && !preview) { try { Log("failed: " + ex.Message); } catch { } } return 1; }
        finally { foreach (var item in new[] {script, library}) { try { if (File.Exists(item)) File.Delete(item); } catch { } } try { Directory.Delete(root, false); } catch { } }
    }
}