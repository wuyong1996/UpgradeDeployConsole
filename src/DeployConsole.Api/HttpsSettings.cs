using System.Text.RegularExpressions;

namespace DeployConsole;

public static partial class HttpsSettings
{
    public static HttpsInput Validate(HttpsInput input)
    {
        var domain = input.Domain?.Trim().ToLowerInvariant() ?? "";
        var certificate = input.CertificatePath?.Trim() ?? "";
        var key = input.PrivateKeyPath?.Trim() ?? "";
        if (domain.Length is 0 or > 253 || !DomainPattern().IsMatch(domain))
            throw new ConsoleException(400, "默认域名格式无效，请只填写域名，不包含协议、端口或路径");
        foreach (var path in new[] { certificate, key })
            if (path.Length is 0 or > 512 || !PathPattern().IsMatch(path) || path.Split('/').Any(p => p is "." or ".."))
                throw new ConsoleException(400, "请填写证书和私钥在服务器上的完整绝对路径");
        if (certificate == key) throw new ConsoleException(400, "完整证书链和私钥必须使用不同文件");
        return new(domain, certificate, key);
    }

    [GeneratedRegex(@"\A(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\z")]
    private static partial Regex DomainPattern();
    // ACME wildcard certificates can have a literal '*' in directory and file names.
    [GeneratedRegex(@"\A/[A-Za-z0-9_.*-]+(?:/[A-Za-z0-9_.*-]+)+\z")]
    private static partial Regex PathPattern();
}
