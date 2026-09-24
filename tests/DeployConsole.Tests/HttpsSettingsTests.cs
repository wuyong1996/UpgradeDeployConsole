using DeployConsole;
using Xunit;

namespace DeployConsole.Tests;

public sealed class HttpsSettingsTests
{
    [Fact]
    public void NormalizesDomainAndAcceptsAcmeFilePaths()
    {
        var input = HttpsSettings.Validate(new(" WWW.Example.COM ", " /root/.acme.sh/example.com_ecc/fullchain.cer ", "/root/.acme.sh/example.com_ecc/example.com.key"));
        Assert.Equal("www.example.com", input.Domain);
        Assert.Equal("/root/.acme.sh/example.com_ecc/fullchain.cer", input.CertificatePath);
    }

    [Theory]
    [InlineData("/root/.acme.sh/*.halfsuger.top_ecc/fullchain.cer", "/root/.acme.sh/*.halfsuger.top_ecc/*.halfsuger.top.key")]
    [InlineData("/root/.acme.sh/example.com_ecc/fullchain.cer", "/root/.acme.sh/example.com_ecc/example.com.key")]
    public void ExistingAcmeNamesArePreservedLiterally(string certificate, string key)
    {
        var input = HttpsSettings.Validate(new("www.halfsuger.top", certificate, key));
        Assert.Equal(certificate, input.CertificatePath);
        Assert.Equal(key, input.PrivateKeyPath);
    }

    [Theory]
    [InlineData("https://example.com", "/etc/acme/key.pem")]
    [InlineData("example.com:443", "/etc/acme/key.pem")]
    [InlineData("*.example.com", "/etc/acme/key.pem")]
    [InlineData("127.0.0.1", "/etc/acme/key.pem")]
    [InlineData("example.com", "/etc/../key.pem")]
    [InlineData("example.com", "/etc/acme/key.pem;bad")]
    [InlineData("example.com", "/etc/acme/fullchain.pem")]
    public void InvalidDomainOrPathIsRejected(string domain, string key)
    {
        var failure = Assert.Throws<ConsoleException>(() => HttpsSettings.Validate(new(domain, "/etc/acme/fullchain.pem", key)));
        Assert.Equal(400, failure.Status);
    }
}
