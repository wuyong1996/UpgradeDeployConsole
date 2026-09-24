using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.Extensions.Options;

namespace DeployConsole;

public sealed class GitCredentials
{
    private readonly IDataProtector protector;
    private readonly byte[] hashKey;
    public GitCredentials(IOptions<ConsoleOptions> options)
    {
        var directory = Directory.CreateDirectory(Path.Combine(options.Value.DataDirectory, "git-keys"));
        if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(directory.FullName, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        protector = DataProtectionProvider.Create(directory).CreateProtector("DeployConsole.GitCredentials.v1");
        var file = Path.Combine(directory.FullName, "fingerprint.key");
        if (!File.Exists(file))
        {
            var bytes = protector.Protect(RandomNumberGenerator.GetBytes(32));
            var fileOptions = new FileStreamOptions { Mode = FileMode.CreateNew, Access = FileAccess.Write };
            if (!OperatingSystem.IsWindows()) fileOptions.UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite;
            using var stream = new FileStream(file, fileOptions);
            stream.Write(bytes); stream.Flush(true);
        }
        hashKey = protector.Unprotect(File.ReadAllBytes(file));
    }
    public string Hash(object value) => Convert.ToHexString(HMACSHA256.HashData(hashKey, Encoding.UTF8.GetBytes(JsonSerializer.Serialize(value, StateStore.Json))));
    public string Protect(GitCredential credential) => protector.Protect(JsonSerializer.Serialize(credential, StateStore.Json));
    public GitCredential? Read(ConsoleState state, DeploymentProject project)
    {
        if (project.GitAuthMode != "https") return null;
        if (!state.GitSecrets.TryGetValue(project.Id, out var encrypted)) throw new ConsoleException(409, "Git凭据不可用，请重新填写");
        try
        {
            var credential = JsonSerializer.Deserialize<GitCredential>(protector.Unprotect(encrypted), StateStore.Json);
            if (credential is null || credential.Repository != project.Repository || credential.Username != project.GitUsername) throw new CryptographicException();
            return credential;
        }
        catch (Exception ex) when (ex is CryptographicException or JsonException) { throw new ConsoleException(409, "Git凭据不可用，请重新填写"); }
    }
    public GitCredential? Resolve(ConsoleState state, Guid? id, string repository, GitAuthInput? input)
    {
        var project = id.HasValue ? ProjectService.Find(state, id.Value) : null;
        if (input is null) return project?.Repository == repository ? Read(state, project) : null;
        if (input.Mode == "none") return null;
        if (input.Mode != "https" || !repository.StartsWith("https://", StringComparison.OrdinalIgnoreCase)) throw new ConsoleException(400, "用户名和密码或Token仅适用于HTTPS仓库");
        if (string.IsNullOrWhiteSpace(input.Username) || input.Username.Length > 200 || input.Username.Any(c => char.IsControl(c) || c == ':')) throw new ConsoleException(400, "请填写有效Git用户名");
        if (input.UseStored)
        {
            if (input.Secret is { Length: > 0 } || project is null || project.Repository != repository || project.GitUsername != input.Username || project.GitAuthMode != "https") throw new ConsoleException(400, "仓库或账号已变化，请重新填写密码或Token");
            return Read(state, project);
        }
        if (string.IsNullOrEmpty(input.Secret) || input.Secret.Length > 4096 || input.Secret.Any(char.IsControl)) throw new ConsoleException(400, "请填写有效Git密码或Token");
        return new(repository, input.Username, input.Secret);
    }
}
