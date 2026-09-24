using System.Text.Json;
using DeployConsole;
using Microsoft.Extensions.Options;
using Xunit;

namespace DeployConsole.Tests;

public sealed class GitTests : IDisposable
{
    private const string Repository = "https://github.com/example/project.git";
    private const string Secret = "unit-test-only-token-123";
    private readonly string directory = Path.Combine(Path.GetTempPath(), "console-git-" + Guid.NewGuid().ToString("N"));
    private readonly StateStore store;
    private readonly GitCredentials credentials;
    private readonly BranchProofStore proofs;
    private readonly ProjectService projects;
    private readonly GitRepositoryService git;
    private readonly FakeReader reader = new();
    private readonly Clock clock = new();
    private readonly IOptions<ConsoleOptions> options;
    public GitTests()
    {
        options = Options.Create(new ConsoleOptions { DataDirectory = directory, TargetsDirectory = Path.Combine(directory, "targets") });
        store = new(options); credentials = new(options); proofs = new(credentials, clock);
        var targets = new TargetCatalog(options);
        projects = new(store, targets, clock, credentials, proofs);
        git = new(store, targets, credentials, proofs, reader, new NoHost(), options);
    }
    private static GitAuthInput Auth() => new("https", "git-user", Secret);
    private static ProjectInput Input(GitAuthInput? auth = null, string? verification = null) => new(0, "project", "Git test", "test", Repository, "master", "/www/wwwroot/project/current", "/opt/project/current", "/opt/project/repository", auth, verification);
    private async Task<DeploymentProject> CreateAsync()
    {
        var result = await git.BranchesAsync(new(Repository, "project", GitAuth: Auth()), CancellationToken.None);
        return await projects.SaveAsync(null, Input(Auth(), result.Verification), "create", "test");
    }
    [Fact]
    public void ParsesOnlyBranchesAndRepositoryDefault()
    {
        var output = $"ref: refs/heads/master\tHEAD\n{new string('a', 40)}\trefs/heads/master\n{new string('b', 40)}\trefs/heads/feature/login\n{new string('c', 40)}\trefs/tags/v1\ninvalid\trefs/heads/invalid\n";
        var result = GitBranchReader.Parse(output);
        Assert.Equal("master", result.DefaultBranch); Assert.Equal(2, result.Branches.Count);
        Assert.Contains(result.Branches, x => x.Name == "feature/login");
        Assert.Empty(GitBranchReader.Parse("").Branches);
    }
    [Fact]
    public async Task SavesEncryptedCredentialWithoutReturningOrLoggingIt()
    {
        var project = await CreateAsync();
        Assert.Equal("https", project.GitAuthMode); Assert.Equal("git-user", project.GitUsername);
        Assert.Equal(Secret, reader.Credential?.Secret);
        Assert.DoesNotContain(Secret, JsonSerializer.Serialize(project, StateStore.Json));
        Assert.DoesNotContain(Secret, File.ReadAllText(Path.Combine(directory, "state.json")));
        Assert.Equal(Secret, (await store.ReadAsync(s => new GitCredentials(options).Read(s, project)))?.Secret);
        Assert.DoesNotContain(Secret, JsonSerializer.Serialize(await store.ReadAsync(s => s.Audit), StateStore.Json));
    }
    [Fact]
    public async Task ReusesStoredCredentialOnlyForExactRepositoryAndUsername()
    {
        var project = await CreateAsync();
        var reuse = new GitAuthInput("https", "git-user", UseStored: true);
        await git.BranchesAsync(new(Repository, "project", project.Id, reuse), CancellationToken.None);
        Assert.Equal(Secret, reader.Credential?.Secret);
        await Assert.ThrowsAsync<ConsoleException>(() => git.BranchesAsync(new("https://github.com/other/repo.git", "project", project.Id, reuse), CancellationToken.None));
        await Assert.ThrowsAsync<ConsoleException>(() => git.BranchesAsync(new(Repository, "project", project.Id, reuse with { Username = "other" }), CancellationToken.None));
    }
    [Fact]
    public async Task RejectsMissingProofMissingBranchAndChangedCredentials()
    {
        await Assert.ThrowsAsync<ConsoleException>(() => projects.SaveAsync(null, Input(Auth()), "missing-proof", "test"));
        var result = await git.BranchesAsync(new(Repository, "project", GitAuth: Auth()), CancellationToken.None);
        await Assert.ThrowsAsync<ConsoleException>(() => projects.SaveAsync(null, Input(Auth(), result.Verification) with { Branch = "not-a-branch" }, "invalid-branch", "test"));
        await Assert.ThrowsAsync<ConsoleException>(() => projects.SaveAsync(null, Input(Auth() with { Secret = "different" }, result.Verification), "changed-auth", "test"));
        Assert.Empty(await store.ReadAsync(s => s.Projects)); Assert.Empty(await store.ReadAsync(s => s.GitSecrets));
    }
    [Fact]
    public async Task ExpiredProofRequiresAnotherQuery()
    {
        var result = await git.BranchesAsync(new(Repository, "project", GitAuth: Auth()), CancellationToken.None);
        clock.Now = clock.Now.AddMinutes(11);
        await Assert.ThrowsAsync<ConsoleException>(() => projects.SaveAsync(null, Input(Auth(), result.Verification), "expired", "test"));
    }
    [Fact]
    public async Task RemovingCredentialsClearsSecretAndPausesPlans()
    {
        var project = await CreateAsync();
        await store.WriteAsync(s => { var p = ProjectService.Find(s, project.Id); p.Front.AutoDeploy = p.Back.AutoDeploy = true; return true; });
        var auth = new GitAuthInput();
        var result = await git.BranchesAsync(new(Repository, "project", project.Id, auth), CancellationToken.None);
        var saved = await projects.SaveAsync(project.Id, Input(auth, result.Verification) with { Version = project.Version }, "remove-auth", "test");
        Assert.Equal("none", saved.GitAuthMode); Assert.Empty(saved.GitUsername);
        Assert.False(saved.Front.AutoDeploy || saved.Back.AutoDeploy); Assert.Empty(await store.ReadAsync(s => s.GitSecrets));
    }
    [Fact]
    public async Task IdempotentReplayDoesNotDuplicateCredentialsAndDetectsSecretChanges()
    {
        var result = await git.BranchesAsync(new(Repository, "project", GitAuth: Auth()), CancellationToken.None);
        var input = Input(Auth(), result.Verification);
        var first = await projects.SaveAsync(null, input, "same", "test");
        var repeat = await projects.SaveAsync(null, input, "same", "test");
        Assert.Equal(first.Id, repeat.Id); Assert.Single(await store.ReadAsync(s => s.GitSecrets));
        await Assert.ThrowsAsync<ConsoleException>(() => projects.SaveAsync(null, input with { GitAuth = Auth() with { Secret = "changed" } }, "same", "test"));
    }
    [Theory]
    [InlineData("file:///etc/passwd")]
    [InlineData("https://git-user:token@github.com/repo.git")]
    [InlineData("https://127.0.0.1/repo.git")]
    [InlineData("https://github.com:8080/repo.git")]
    public async Task RejectsUnapprovedDestinationsBeforeUsingCredentials(string repository)
    {
        await Assert.ThrowsAsync<ConsoleException>(() => git.BranchesAsync(new(repository, "project", GitAuth: Auth()), CancellationToken.None));
        Assert.Equal(0, reader.Calls);
    }
    public void Dispose() { git.Dispose(); store.Dispose(); Directory.Delete(directory, true); }
    private sealed class Clock : TimeProvider { public DateTimeOffset Now { get; set; } = new(2026, 9, 23, 9, 0, 0, TimeSpan.Zero); public override DateTimeOffset GetUtcNow() => Now; }
    private sealed class FakeReader : IGitBranchReader
    {
        public int Calls { get; private set; }
        public GitCredential? Credential { get; private set; }
        public Task<HostResult> ReadAsync(string repository, GitCredential? credential, CancellationToken cancellationToken) { Calls++; Credential = credential; return Task.FromResult(new HostResult { Ok = true, DefaultBranch = "master", Branches = [new("master", new string('a', 40)), new("feature/login", new string('b', 40))] }); }
    }
    private sealed class NoHost : IHostControl { public Task<HostResult> RunAsync(HostRequest request, Func<string, Task>? progress = null) => throw new InvalidOperationException("No service operations in Git tests"); }
}
