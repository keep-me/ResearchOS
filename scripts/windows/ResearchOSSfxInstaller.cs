using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;

internal static class ResearchOSSfxInstaller
{
    private const string MarkerText = "\nRESEARCHOS_SFX_PAYLOAD_V1\n";

    private static int Main()
    {
        string selfPath = Assembly.GetExecutingAssembly().Location;
        byte[] marker = Encoding.ASCII.GetBytes(MarkerText);
        long markerOffset = FindMarker(selfPath, marker);
        if (markerOffset < 0)
        {
            Console.Error.WriteLine("ResearchOS setup payload was not found.");
            return 2;
        }

        string extractDir = Path.Combine(Path.GetTempPath(), "ResearchOSSetup_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(extractDir);
        string payloadZip = Path.Combine(extractDir, "payload.zip");

        try
        {
            CopyPayload(selfPath, markerOffset + marker.Length, payloadZip);
            ZipFile.ExtractToDirectory(payloadZip, extractDir);

            string installer = Path.Combine(extractDir, "install-researchos.cmd");
            if (!File.Exists(installer))
            {
                Console.Error.WriteLine("install-researchos.cmd was not found in setup payload.");
                return 3;
            }

            ProcessStartInfo startInfo = new ProcessStartInfo
            {
                FileName = "cmd.exe",
                Arguments = "/c \"" + installer + "\"",
                WorkingDirectory = extractDir,
                UseShellExecute = false,
            };

            using (Process process = Process.Start(startInfo))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        finally
        {
            try
            {
                Directory.Delete(extractDir, true);
            }
            catch
            {
                // The installer can still be closing handles; temp cleanup is best effort.
            }
        }
    }

    private static long FindMarker(string path, byte[] marker)
    {
        using (FileStream stream = File.OpenRead(path))
        {
            int matched = 0;
            long offset = 0;
            int value;
            while ((value = stream.ReadByte()) != -1)
            {
                byte current = (byte)value;
                if (current == marker[matched])
                {
                    matched++;
                    if (matched == marker.Length)
                    {
                        return offset - marker.Length + 1;
                    }
                }
                else
                {
                    matched = current == marker[0] ? 1 : 0;
                }
                offset++;
            }
        }
        return -1;
    }

    private static void CopyPayload(string sourcePath, long payloadOffset, string payloadPath)
    {
        using (FileStream source = File.OpenRead(sourcePath))
        using (FileStream target = File.Create(payloadPath))
        {
            source.Position = payloadOffset;
            source.CopyTo(target);
        }
    }
}
