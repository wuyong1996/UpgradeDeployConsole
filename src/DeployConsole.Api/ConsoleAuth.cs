using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Extensions.Options;

namespace DeployConsole;

public sealed class ConsoleAuth(IOptions<ConsoleOptions> options, TimeProvider clock) : IDisposable
{
    private sealed record Session(string PasswordFingerprint, string Csrf, DateTimeOffset ExpiresAt);
    private readonly ConcurrentDictionary<string, Session> sessions = new();
    private readonly SemaphoreSlim fileGate = new(1);
    private string? fingerprint;
    public const string CookieName = "deploy_console_session";

    private async Task<string> PasswordAsync()
    {
        await fileGate.WaitAsync();
        try
        {
            var path = options.Value.PasswordFile;
            if (!File.Exists(path) || new FileInfo(path).Length > 4096) throw new IOException();
            var value = (await File.ReadAllTextAsync(path, Encoding.UTF8)).TrimStart('\uFEFF').TrimEnd('\r', '\n');
            if (value.Length == 0 || value.Contains('\n') || value.Contains('\r')) throw new IOException();
            var current = Hash(value);
            if (current != fingerprint) { sessions.Clear(); fingerprint = current; }
            return value;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            fingerprint = null;
            sessions.Clear();
            throw new ConsoleException(503, "访问配置不可用，请在服务器检查密码文件");
        }
        finally { fileGate.Release(); }
    }

    public async Task<object> LoginAsync(HttpContext context, string password)
    {
        var expected = await PasswordAsync();
        if (password.Length > 4096 || !CryptographicOperations.FixedTimeEquals(SHA256.HashData(Encoding.UTF8.GetBytes(expected)), SHA256.HashData(Encoding.UTF8.GetBytes(password))))
            throw new ConsoleException(401, "密码错误");
        var token = Convert.ToHexString(RandomNumberGenerator.GetBytes(32));
        var session = new Session(Hash(expected), Convert.ToHexString(RandomNumberGenerator.GetBytes(32)), clock.GetUtcNow().AddHours(8));
        foreach (var expired in sessions.Where(x => x.Value.ExpiresAt <= clock.GetUtcNow())) sessions.TryRemove(expired.Key, out _);
        if (sessions.Count >= 1000) throw new ConsoleException(429, "登录会话过多，请稍后重试");
        sessions[token] = session;
        context.Response.Cookies.Append(CookieName, token, new CookieOptions { HttpOnly = true, Secure = !options.Value.AllowLoopbackHttp, SameSite = SameSiteMode.Strict, Path = "/", Expires = session.ExpiresAt });
        return new { csrfToken = session.Csrf, expiresAt = session.ExpiresAt };
    }

    public async Task<object> ValidateAsync(HttpContext context, bool write)
    {
        await PasswordAsync();
        var token = context.Request.Cookies[CookieName];
        if (token is null || !sessions.TryGetValue(token, out var session) || session.ExpiresAt <= clock.GetUtcNow() || session.PasswordFingerprint != fingerprint)
            throw new ConsoleException(401, "请重新登录");
        if (write && !CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(context.Request.Headers["X-CSRF-Token"].ToString()), Encoding.UTF8.GetBytes(session.Csrf)))
            throw new ConsoleException(403, "请求验证失败，请刷新后重试");
        return new { csrfToken = session.Csrf, expiresAt = session.ExpiresAt };
    }

    public void Logout(HttpContext context)
    {
        if (context.Request.Cookies[CookieName] is { } token) sessions.TryRemove(token, out _);
        context.Response.Cookies.Delete(CookieName, new CookieOptions { Path = "/", Secure = !options.Value.AllowLoopbackHttp, SameSite = SameSiteMode.Strict });
    }
    private static string Hash(string text) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(text)));
    public void Dispose() => fileGate.Dispose();
}
