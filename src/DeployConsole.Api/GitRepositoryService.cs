using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using Microsoft.Extensions.Options;

namespace DeployConsole;

public sealed class BranchProofStore(GitCredentials credentials, TimeProvider clock)
{
    private sealed record Proof(string Binding, string[] Branches, DateTimeOffset ExpiresAt);
    private readonly ConcurrentDictionary<string, Proof> proofs = new();
    private string Binding(Guid? id, string slug, string repository, GitCredential? credential) => credentials.Hash(new { id, slug, repository, credential });
    public string Add(GitBranchesInput input, GitCredential? credential, IEnumerable<string> branches)
    {
        foreach (var item in proofs.Where(x => x.Value.ExpiresAt <= clock.GetUtcNow())) proofs.TryRemove(item.Key, out _);
        if (proofs.Count >= 1000) throw new ConsoleException(429, "分支查询较多，请稍后重试");
        var token = Guid.NewGuid().ToString("N");
        proofs[token] = new(Binding(input.ProjectId, input.Slug, input.Repository, credential), branches.ToArray(), clock.GetUtcNow().AddMinutes(10));
        return token;
    }
    public void Validate(Guid? id, ProjectInput input, GitCredential? credential)
    {
        // Old clients without Git authentication keep their existing API contract.
        if (input.GitAuth is null && input.BranchVerification is null) return;
        if (input.BranchVerification is null || !proofs.TryGetValue(input.BranchVerification, out var proof) || proof.ExpiresAt <= clock.GetUtcNow() || proof.Binding != Binding(id, input.Slug, input.Repository, credential) || !proof.Branches.Contains(input.Branch, StringComparer.Ordinal))
            throw new ConsoleException(409, "分支信息已失效，请重新获取远端分支后选择");
    }
}

public interface IGitBranchReader
{
    Task<HostResult> ReadAsync(string repository, GitCredential? credential, CancellationToken cancellationToken);
}

public sealed class GitBranchReader : IGitBranchReader
{
    public async Task<HostResult> ReadAsync(string repository, GitCredential? credential, CancellationToken cancellationToken)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(30));
        var start = new ProcessStartInfo(OperatingSystem.IsWindows() ? "git" : "/usr/bin/git") { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = Path.GetTempPath() };
        foreach (var key in start.Environment.Keys.Where(x => x.StartsWith("GIT_", StringComparison.OrdinalIgnoreCase) || x.StartsWith("SSH_", StringComparison.OrdinalIgnoreCase)).ToArray()) start.Environment.Remove(key);
        start.Environment["GIT_TERMINAL_PROMPT"] = "0";
        start.Environment["GIT_CONFIG_NOSYSTEM"] = "1";
        start.Environment["GIT_CONFIG_GLOBAL"] = OperatingSystem.IsWindows() ? "NUL" : "/dev/null";
        start.Environment["GIT_ALLOW_PROTOCOL"] = "https";
        foreach (var argument in new[] { "-c", "credential.helper=", "-c", "http.followRedirects=false", "-c", "http.sslVerify=true", "ls-remote", "--symref", "--", repository, "HEAD", "refs/heads/*" }) start.ArgumentList.Add(argument);
        if (credential is not null)
        {
            start.Environment["GIT_CONFIG_COUNT"] = "1";
            start.Environment["GIT_CONFIG_KEY_0"] = $"http.{repository}.extraHeader";
            start.Environment["GIT_CONFIG_VALUE_0"] = "Authorization: Basic " + Convert.ToBase64String(Encoding.UTF8.GetBytes(credential.Username + ":" + credential.Secret));
        }
        using var process = new Process { StartInfo = start };
        try
        {
            process.Start();
            var output = ReadOutputAsync(process.StandardOutput, timeout.Token);
            var errors = DrainAsync(process.StandardError, timeout.Token);
            await Task.WhenAll(process.WaitForExitAsync(timeout.Token), output, errors);
            if (process.ExitCode != 0) throw new ConsoleException(422, "无法读取远端分支，请检查仓库地址、网络和Git认证；GitHub需要使用Token");
            return Parse(await output);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested) { throw new ConsoleException(504, "获取分支超时，请检查服务器到Git仓库的网络后重试"); }
        catch (System.ComponentModel.Win32Exception) { throw new ConsoleException(503, "服务器尚未安装Git，无法获取分支"); }
        finally { try { if (!process.HasExited) process.Kill(true); } catch (InvalidOperationException) { } }
    }
    private static async Task<string> ReadOutputAsync(StreamReader reader, CancellationToken cancellationToken)
    {
        var result = new StringBuilder(); var buffer = new char[4096];
        while (await reader.ReadAsync(buffer, cancellationToken) is var count && count > 0)
        {
            if (result.Length + count > 1048576) throw new ConsoleException(422, "远端分支响应超过限制");
            result.Append(buffer, 0, count);
        }
        return result.ToString();
    }
    private static async Task DrainAsync(StreamReader reader, CancellationToken cancellationToken) { var buffer = new char[4096]; while (await reader.ReadAsync(buffer, cancellationToken) > 0) { } }
    public static HostResult Parse(string output)
    {
        var result = new HostResult { Ok = true };
        foreach (var line in output.Split('\n', StringSplitOptions.RemoveEmptyEntries))
        {
            var parts = line.TrimEnd('\r').Split('\t');
            if (parts.Length != 2) continue;
            if (parts[1] == "HEAD" && parts[0].StartsWith("ref: refs/heads/", StringComparison.Ordinal)) result.DefaultBranch = parts[0][16..];
            if (parts[1].StartsWith("refs/heads/", StringComparison.Ordinal) && ProjectService.Match(parts[0], "^[0-9a-f]{40}(?:[0-9a-f]{24})?$"))
            {
                var name = parts[1][11..];
                if (ProjectService.ValidBranch(name)) result.Branches.Add(new(name, parts[0]));
            }
        }
        if (result.Branches.Count > 5000) throw new ConsoleException(422, "远端分支数量超过限制");
        result.Branches = result.Branches.DistinctBy(x => x.Name).OrderBy(x => x.Name, StringComparer.Ordinal).ToList();
        return result;
    }
}

public sealed class GitRepositoryService(StateStore store, TargetCatalog targets, GitCredentials credentials, BranchProofStore proofs, IGitBranchReader reader, IHostControl host, IOptions<ConsoleOptions> options) : IDisposable
{
    private readonly SemaphoreSlim gate = new(2);
    public void Dispose() => gate.Dispose();
    public async Task<GitBranchesResult> BranchesAsync(GitBranchesInput input, CancellationToken cancellationToken)
    {
        ProjectService.ValidateRepository(input.Repository);
        if (string.IsNullOrEmpty(input.Slug) || !ProjectService.Match(input.Slug, "^[a-z][a-z0-9-]{0,63}$")) throw new ConsoleException(400, "请先填写有效项目标识");
        var uri = new Uri(input.Repository);
        var target = targets.Find(input.Slug);
        var hosts = options.Value.AllowedGitHosts.Concat(target?.AllowedGitHosts ?? []);
        if (!hosts.Contains(uri.Host, StringComparer.OrdinalIgnoreCase)) throw new ConsoleException(400, "Git主机尚未允许，请在服务器Console:AllowedGitHosts或项目接入文件中配置");
        var credential = await store.ReadAsync(s => credentials.Resolve(s, input.ProjectId, input.Repository, input.GitAuth));
        if (!await gate.WaitAsync(0, cancellationToken)) throw new ConsoleException(429, "已有分支查询正在进行，请稍后重试");
        try
        {
            HostResult result;
            if (uri.Scheme == "ssh")
            {
                if (target is null || !OperatingSystem.IsLinux()) throw new ConsoleException(409, "SSH分支查询需要先在Linux服务器接入项目，并为构建账号配置密钥与known_hosts；也可使用HTTPS和Token");
                result = await host.RunAsync(new(input.Slug, "branches", "", input.Repository, "main", Guid.NewGuid().ToString()));
                if (!result.Ok) throw new ConsoleException(422, result.Message);
            }
            else result = await reader.ReadAsync(input.Repository, credential, cancellationToken);
            return new(result.Branches, result.DefaultBranch, proofs.Add(input, credential, result.Branches.Select(x => x.Name)));
        }
        finally { gate.Release(); }
    }
}
