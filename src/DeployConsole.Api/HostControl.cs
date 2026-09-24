using System.Diagnostics;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace DeployConsole;

public sealed class TargetCatalog(IOptions<ConsoleOptions> options)
{
    public IReadOnlyList<TargetSummary> List()
    {
        if (!Directory.Exists(options.Value.TargetsDirectory)) return [];
        return Directory.EnumerateFiles(options.Value.TargetsDirectory, "*.json").Select(Read).ToArray();
    }
    public TargetSummary? Find(string slug) => List().SingleOrDefault(x => x.Slug == slug);
    private static TargetSummary Read(string path)
    {
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        var root = document.RootElement;
        TargetService? Side(string name) => root.TryGetProperty(name, out var side) && side.ValueKind != JsonValueKind.Null ? JsonSerializer.Deserialize<TargetService>(side, StateStore.Json) : null;
        var database = root.TryGetProperty("database", out var db) ? db : default;
        var managed = database.ValueKind == JsonValueKind.Object && database.TryGetProperty("managed", out var flag) && flag.GetBoolean();
        return new(root.GetProperty("slug").GetString()!, Side("front"), Side("back"), database.ValueKind == JsonValueKind.Object ? database.GetProperty("kind").GetString()! : "none",
            database.ValueKind == JsonValueKind.Object && database.TryGetProperty("identity", out var identity) ? identity.GetString() : null,
            managed, root.GetProperty("repositoryPath").GetString()!, root.GetProperty("allowedGitHosts").EnumerateArray().Select(x => x.GetString()!).ToArray());
    }
}

public interface IHostControl
{
    Task<HostResult> RunAsync(HostRequest request, Func<string, Task>? progress = null);
}

public sealed class LinuxHostControl(IOptions<ConsoleOptions> options, StateStore store, GitCredentials credentials) : IHostControl
{
    public async Task<HostResult> RunAsync(HostRequest request, Func<string, Task>? progress = null)
    {
        if (!OperatingSystem.IsLinux()) return new() { Ok = false, Message = "当前为非 Linux 环境，服务操作未执行" };
        if (request.Action is "check" or "deploy" or "prepare" or "start-all")
        {
            var credential = await store.ReadAsync(state => state.Projects.SingleOrDefault(p => p.Slug == request.Slug && p.Repository == request.Repository) is { } project ? credentials.Read(state, project) : null);
            request = request with { Credential = credential };
        }
        using var process = new Process { StartInfo = new ProcessStartInfo("/usr/bin/sudo") { RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true, UseShellExecute = false } };
        process.StartInfo.ArgumentList.Add("-n");
        process.StartInfo.ArgumentList.Add(options.Value.HelperPath);
        process.Start();
        // Drain stderr without returning raw commands, paths, credentials or Git output.
        var errors = DrainAsync(process.StandardError);
        await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(request, StateStore.Json));
        process.StandardInput.Close();
        HostResult? result = null;
        while (await process.StandardOutput.ReadLineAsync() is { } line)
        {
            if (line.Length > 131072) continue;
            try
            {
                using var message = JsonDocument.Parse(line);
                if (message.RootElement.TryGetProperty("step", out var step) && progress is not null)
                    await progress(step.GetString() ?? "执行中");
                if (message.RootElement.TryGetProperty("result", out var payload)) result = JsonSerializer.Deserialize<HostResult>(payload, StateStore.Json);
            }
            catch (JsonException) { /* Ignore non-protocol subprocess noise. */ }
        }
        await process.WaitForExitAsync();
        await errors;
        return result ?? new() { Ok = false, Message = "服务器执行入口失败，请检查接入配置及受限 sudo 权限" };
    }
    private static async Task DrainAsync(StreamReader reader) { var buffer = new char[4096]; while (await reader.ReadAsync(buffer) > 0) { } }
}
