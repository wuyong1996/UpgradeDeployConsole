using System.Text.Json;
using DeployConsole;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace DeployConsole.Tests;

public sealed class ConsoleTests : IDisposable
{
    private readonly string directory = Path.Combine(Path.GetTempPath(), "deploy-console-tests-" + Guid.NewGuid().ToString("N"));
    private readonly IOptions<ConsoleOptions> options;
    private readonly StateStore store;
    private readonly TargetCatalog targets;
    private readonly ProjectService projects;
    private readonly JobService jobs;
    private static readonly DateTimeOffset Now = new(2026, 9, 22, 8, 0, 0, TimeSpan.Zero);
    private readonly FixedClock clock = new();
    private static readonly string[] GitHosts = ["github.com"];
    public ConsoleTests()
    {
        Directory.CreateDirectory(directory);
        options = Options.Create(new ConsoleOptions { DataDirectory = directory, TargetsDirectory = Path.Combine(directory, "targets"), PasswordFile = Path.Combine(directory, "password.txt"), AllowLoopbackHttp = true });
        Directory.CreateDirectory(options.Value.TargetsDirectory);
        File.WriteAllText(options.Value.PasswordFile, "admin\n");
        store = new(options); targets = new(options); var credentials = new GitCredentials(options); projects = new(store, targets, clock, credentials, new(credentials, clock)); jobs = new(store, targets, clock);
        Bind();
    }
    private void Bind(bool managed = true)
    {
        File.WriteAllText(Path.Combine(options.Value.TargetsDirectory, "project.json"), JsonSerializer.Serialize(new {
            slug = "project", repositoryPath = "/opt/project/repository", allowedGitHosts = GitHosts,
            front = new { kind = "nginx", port = 8080, current = "/www/wwwroot/project/current", independentDeploy = true },
            back = new { kind = "systemd", unit = "project-api.service", port = 5080, current = "/opt/project/current", independentDeploy = true },
            database = new { kind = managed ? "systemd" : "external", identity = "mysql.service", managed }
        }));
    }
    private static ProjectInput Input() => new(0, "project", "示例项目", "test", "https://github.com/team/project.git", "main", "/www/wwwroot/project/current", "/opt/project/current", "/opt/project/repository");
    private Task<DeploymentProject> AddAsync() => projects.SaveAsync(null, Input(), Guid.NewGuid().ToString(), "test");
    private async Task<DeploymentProject> GetAsync(Guid id) => await store.ReadAsync(s => ProjectService.Find(s, id));

    [Fact]
    public async Task PasswordRotationInvalidatesSessionsAndMissingFileFailsClosed()
    {
        using var auth = new ConsoleAuth(options, clock);
        var login = new DefaultHttpContext();
        await auth.LoginAsync(login, "admin");
        var cookie = login.Response.Headers.SetCookie.ToString().Split(';')[0];
        var request = new DefaultHttpContext(); request.Request.Headers.Cookie = cookie;
        await auth.ValidateAsync(request, false);
        var csrfFailure = await Assert.ThrowsAsync<ConsoleException>(() => auth.ValidateAsync(request, true)); Assert.Equal(403, csrfFailure.Status);
        File.WriteAllText(options.Value.PasswordFile, "changed\n");
        var rotated = await Assert.ThrowsAsync<ConsoleException>(() => auth.ValidateAsync(request, false)); Assert.Equal(401, rotated.Status);
        File.Delete(options.Value.PasswordFile);
        var missing = await Assert.ThrowsAsync<ConsoleException>(() => auth.LoginAsync(new DefaultHttpContext(), "admin")); Assert.Equal(503, missing.Status);
    }
    [Fact]
    public async Task ValidCsrfAllowsWriteAndWrongPasswordDoesNotCreateSession()
    {
        using var auth = new ConsoleAuth(options, clock);
        var context = new DefaultHttpContext();
        var error = await Assert.ThrowsAsync<ConsoleException>(() => auth.LoginAsync(context, "wrong")); Assert.Equal(401, error.Status); Assert.False(context.Response.Headers.ContainsKey("Set-Cookie"));
        var login = await auth.LoginAsync(context, "admin");
        using var json = JsonDocument.Parse(JsonSerializer.Serialize(login));
        var request = new DefaultHttpContext(); request.Request.Headers.Cookie = context.Response.Headers.SetCookie.ToString().Split(';')[0]; request.Request.Headers["X-CSRF-Token"] = json.RootElement.GetProperty("csrfToken").GetString();
        await auth.ValidateAsync(request, true);
    }
    [Fact]
    public async Task StoppingFrontPreservesBackPlanAndCancelsOnlyFrontQueue()
    {
        var project = await AddAsync();
        project = await projects.ScheduleAsync(project.Id, "front", new(project.Version, true, 3600), "front-plan", "test");
        project = await projects.ScheduleAsync(project.Id, "back", new(project.Version, true, 86400), "back-plan", "test");
        await store.WriteAsync(s => { s.Jobs.Add(JobService.Create(project, "deploy", "front", Now)); s.Jobs.Add(JobService.Create(project, "deploy", "back", Now)); return true; });
        await jobs.EnqueueAsync(project.Id, new(project.Version, "stop", "front"), "stop-front", "test");
        var actual = await GetAsync(project.Id);
        Assert.True(actual.Front.Stopped); Assert.False(actual.Front.AutoDeploy); Assert.Null(actual.Front.NextRunAt);
        Assert.True(actual.Back.AutoDeploy); Assert.Equal(project.Back.NextRunAt, actual.Back.NextRunAt); Assert.False(actual.Back.Stopped);
        var queued = await store.ReadAsync(s => s.Jobs); Assert.Equal("cancelled", queued[0].State); Assert.Equal("queued", queued[1].State);
    }
    [Fact]
    public async Task NeverDisablesScheduleButPermitsManualDeploy()
    {
        var project = await AddAsync(); project = await projects.ScheduleAsync(project.Id, "front", new(project.Version, true, 0), "never", "test");
        Assert.Null(project.Front.NextRunAt);
        var job = await jobs.EnqueueAsync(project.Id, new(project.Version, "deploy", "front"), "manual", "test"); Assert.Equal("queued", job.State);
    }
    [Fact]
    public async Task StopAllRejectsExternalDatabaseBeforeChangingPlans()
    {
        Bind(false); var project = await AddAsync();
        project = await projects.ScheduleAsync(project.Id, "front", new(project.Version, true, 60), "enable", "test");
        var exception = await Assert.ThrowsAsync<ConsoleException>(() => jobs.EnqueueAsync(project.Id, new(project.Version, "stop-all"), "stop", "test")); Assert.Equal(409, exception.Status);
        Assert.True((await GetAsync(project.Id)).Front.AutoDeploy); Assert.False((await GetAsync(project.Id)).ShutdownRequested);
    }
    [Fact]
    public async Task StopAllPausesBothAndIdempotentReplayDoesNotQueueTwice()
    {
        var project = await AddAsync(); var input = new JobInput(project.Version, "stop-all");
        var first = await jobs.EnqueueAsync(project.Id, input, "stop", "test"); var replay = await jobs.EnqueueAsync(project.Id, input, "stop", "test");
        Assert.Equal(first.Id, replay.Id); Assert.Single(await store.ReadAsync(s => s.Jobs));
        var actual = await GetAsync(project.Id); Assert.True(actual.Front.Stopped && actual.Back.Stopped && actual.ShutdownRequested);
    }
    [Fact]
    public async Task ConcurrentWritesAreRejectedAndSourceChangePausesBoth()
    {
        var project = await AddAsync(); var version = project.Version;
        project = await projects.ScheduleAsync(project.Id, "front", new(version, true, 60), "enable", "test");
        var conflict = await Assert.ThrowsAsync<ConsoleException>(() => projects.ScheduleAsync(project.Id, "back", new(version, true, 60), "old", "test")); Assert.Equal(409, conflict.Status);
        project = await projects.SaveAsync(project.Id, Input() with { Version = project.Version, Branch = "develop" }, "change", "test");
        Assert.False(project.Front.AutoDeploy || project.Back.AutoDeploy); Assert.Null(project.Front.NextRunAt);
    }
    [Theory]
    [InlineData("/opt/project/current/../secret")]
    [InlineData("/opt/project/repository/inside")]
    public void UnsafeOrOverlappingPathsAreRejected(string path) => Assert.Throws<ConsoleException>(() => ProjectService.Validate(Input() with { FrontPath = path }));

    [Fact]
    public async Task SchedulerPublishesEachSideAndKeepsIndependentCadence()
    {
        var project = await AddAsync();
        await store.WriteAsync(s => { var p = ProjectService.Find(s, project.Id); p.Front.AutoDeploy = true; p.Front.PeriodSeconds = 3600; p.Front.NextRunAt = Now.AddSeconds(-7201); p.Back.AutoDeploy = true; p.Back.PeriodSeconds = 86400; p.Back.NextRunAt = Now.AddHours(20); return true; });
        var host = new FakeHost(); using var worker = new DeploymentWorker(store, targets, host, clock, NullLogger<DeploymentWorker>.Instance);
        await worker.StartAsync(CancellationToken.None);
        try { await UntilAsync(async () => (await GetAsync(project.Id)).Front.LastCommit is not null); }
        finally { await worker.StopAsync(CancellationToken.None); }
        var actual = await GetAsync(project.Id);
        Assert.Equal(Now.AddSeconds(3599), actual.Front.NextRunAt); Assert.Equal(Now.AddHours(20), actual.Back.NextRunAt); Assert.Single(host.Requests); Assert.Equal("front", host.Requests[0].Side);
        Assert.Equal("succeeded", Assert.Single(await store.ReadAsync(s => s.Jobs)).State);
        Assert.Single(await store.ReadAsync(s => s.Audit.Where(x => x.Action == "job.succeeded").ToArray()));
    }
    [Theory]
    [InlineData("front")]
    [InlineData("back")]
    public async Task RepeatedUnchangedScheduledChecksOnlyUpdatePlan(string side)
    {
        var project = await AddAsync();
        var previous = JobService.Create(project, "deploy", side, Now.AddDays(-1));
        previous.Trigger = "schedule"; previous.State = "succeeded";
        await store.WriteAsync(s => { s.Jobs.Add(previous); return true; });
        var auditCount = await store.ReadAsync(s => s.Audit.Count);
        var host = new FakeHost { NoChanges = true };
        using var worker = new DeploymentWorker(store, targets, host, clock, NullLogger<DeploymentWorker>.Instance);
        await worker.StartAsync(CancellationToken.None);
        try
        {
            for (var attempt = 0; attempt < 2; attempt++)
            {
                await store.WriteAsync(s =>
                {
                    var plan = ProjectService.Find(s, project.Id).Plan(side);
                    plan.AutoDeploy = true; plan.PeriodSeconds = 60; plan.NextRunAt = Now.AddSeconds(-1); plan.LastCheckAt = null;
                    return true;
                });
                await UntilAsync(async () => (await GetAsync(project.Id)).Plan(side).LastCheckAt == Now);
                var plan = (await GetAsync(project.Id)).Plan(side);
                Assert.Equal(Now.AddSeconds(59), plan.NextRunAt);
                Assert.Equal(new string('a', 40), plan.LastCommit); Assert.Equal("test", plan.LastResult); Assert.True(plan.AutoDeploy);
                Assert.Equal(previous.Id, Assert.Single(await store.ReadAsync(s => s.Jobs)).Id);
                Assert.Equal(auditCount, await store.ReadAsync(s => s.Audit.Count));
            }
        }
        finally { await worker.StopAsync(CancellationToken.None); }
        Assert.Equal(2, host.Requests.Count);
        Assert.All(host.Requests, request => { Assert.Equal("deploy", request.Action); Assert.Equal(side, request.Side); });
        Assert.Null((await GetAsync(project.Id)).Plan(side == "front" ? "back" : "front").LastCheckAt);
        var persisted = JsonSerializer.Deserialize<ConsoleState>(File.ReadAllText(Path.Combine(directory, "state.json")), StateStore.Json)!;
        Assert.Equal(previous.Id, Assert.Single(persisted.Jobs).Id);
        Assert.Equal(auditCount, persisted.Audit.Count);
    }
    [Theory]
    [InlineData("manual", false)]
    [InlineData("save", false)]
    [InlineData("schedule", true)]
    public async Task UnchangedResultStillPreservesManualWorkflowAndFailureRecords(string trigger, bool failed)
    {
        var project = await AddAsync();
        var job = JobService.Create(project, "deploy", "front", Now); job.Trigger = trigger;
        await store.WriteAsync(s =>
        {
            s.Jobs.Add(job);
            var plan = ProjectService.Find(s, project.Id).Front;
            plan.AutoDeploy = true; plan.NextRunAt = Now.AddHours(1); plan.LastCommit = new string('b', 40);
            return true;
        });
        var host = new FakeHost { NoChanges = true, FailedSide = failed ? "front" : null };
        using var worker = new DeploymentWorker(store, targets, host, clock, NullLogger<DeploymentWorker>.Instance);
        await worker.StartAsync(CancellationToken.None);
        try { await UntilAsync(async () => (await GetAsync(project.Id)).Front.LastCheckAt == Now); }
        finally { await worker.StopAsync(CancellationToken.None); }
        var record = Assert.Single(await store.ReadAsync(s => s.Jobs));
        Assert.Equal(job.Id, record.Id); Assert.Equal(failed ? "failed" : "succeeded", record.State); Assert.NotEmpty(record.Events);
        Assert.Single(await store.ReadAsync(s => s.Audit.Where(x => x.Action == "job." + record.State).ToArray()));
        if (failed)
        {
            var plan = (await GetAsync(project.Id)).Front;
            Assert.Equal(Now.AddMinutes(10), plan.NextRunAt); Assert.Equal(new string('b', 40), plan.LastCommit);
        }
    }
    [Fact]
    public void HostResultsWithoutNoChangesFlagAreNotAssumedToBeIdleChecks()
    {
        var result = JsonSerializer.Deserialize<HostResult>("{\"ok\":true,\"message\":\"当前服务已是最新提交\"}", StateStore.Json)!;
        Assert.False(result.NoChanges);
    }
    [Fact]
    public async Task StartAfterStopDoesNotReenableAutoPublish()
    {
        var project = await AddAsync();
        await jobs.EnqueueAsync(project.Id, new(project.Version, "stop-all"), "stop", "test");
        var host = new FakeHost(); using var worker = new DeploymentWorker(store, targets, host, clock, NullLogger<DeploymentWorker>.Instance);
        await worker.StartAsync(CancellationToken.None);
        try
        {
            await UntilAsync(async () => (await store.ReadAsync(s => s.Jobs)).All(x => x.State == "succeeded"));
            project = await GetAsync(project.Id); await jobs.EnqueueAsync(project.Id, new(project.Version, "start", "front"), "start", "test");
            await UntilAsync(async () => !(await GetAsync(project.Id)).Front.Stopped);
            var actual = await GetAsync(project.Id); Assert.False(actual.Front.AutoDeploy); Assert.Null(actual.Front.NextRunAt); Assert.True(actual.Back.Stopped);
        }
        finally { await worker.StopAsync(CancellationToken.None); }
    }
    private static async Task UntilAsync(Func<Task<bool>> predicate)
    {
        for (var i = 0; i < 120; i++) { if (await predicate()) return; await Task.Delay(50); }
        Assert.Fail("Worker did not reach expected state");
    }
    [Fact]
    public async Task StartAllRestoresStoppedProjectWithoutResumingSchedulesAndReplayQueuesOnce()
    {
        var project = await AddAsync();
        var host = new FakeHost(); using var worker = new DeploymentWorker(store, targets, host, clock, NullLogger<DeploymentWorker>.Instance);
        await jobs.EnqueueAsync(project.Id, new(project.Version, "stop-all"), "stop", "test");
        await worker.StartAsync(CancellationToken.None);
        try
        {
            await UntilAsync(async () => (await store.ReadAsync(s => s.Jobs)).All(x => x.State == "succeeded"));
            project = await GetAsync(project.Id);
            var input = new JobInput(project.Version, "start-all");
            var first = await jobs.EnqueueAsync(project.Id, input, "resume", "test");
            var replay = await jobs.EnqueueAsync(project.Id, input, "resume", "test");
            Assert.Equal(first.Id, replay.Id);
            await UntilAsync(async () => !(await GetAsync(project.Id)).ShutdownRequested);
            var actual = await GetAsync(project.Id);
            Assert.False(actual.Front.Stopped || actual.Back.Stopped || actual.Front.AutoDeploy || actual.Back.AutoDeploy);
            Assert.Null(actual.Front.NextRunAt); Assert.Null(actual.Back.NextRunAt);
            Assert.Equal(Now.ToUnixTimeMilliseconds(), Assert.Single(host.Requests, x => x.Action == "start-all").RequestedAtUnixMs);
            var publish = await jobs.EnqueueAsync(project.Id, new(actual.Version, "publish"), "publish-again", "test");
            Assert.Equal("prepare", publish.Action);
        }
        finally { await worker.StopAsync(CancellationToken.None); }
    }
    [Fact]
    public async Task FailedStartAllKeepsShutdownAndSchedulesPaused()
    {
        var project = await AddAsync();
        await store.WriteAsync(s => { var p = ProjectService.Find(s, project.Id); p.ShutdownRequested = true; p.Front.Stopped = p.Back.Stopped = true; return true; });
        var job = await jobs.EnqueueAsync(project.Id, new(project.Version, "start-all"), "resume", "test");
        var host = new FakeHost { FailedSide = "" }; using var worker = new DeploymentWorker(store, targets, host, clock, NullLogger<DeploymentWorker>.Instance);
        await worker.StartAsync(CancellationToken.None);
        try { await UntilAsync(async () => (await store.ReadAsync(s => s.Jobs)).Single(x => x.Id == job.Id).State == "failed"); }
        finally { await worker.StopAsync(CancellationToken.None); }
        var actual = await GetAsync(project.Id);
        Assert.True(actual.ShutdownRequested && actual.Front.Stopped && actual.Back.Stopped);
        Assert.False(actual.Front.AutoDeploy || actual.Back.AutoDeploy);
    }
    [Theory]
    [InlineData("stop-all", "")]
    [InlineData("stop", "back")]
    public async Task NewStopDuringStartAllCannotBeOverwrittenByLateSuccess(string action, string side)
    {
        var project = await AddAsync();
        var host = new PausingHost { PausedAction = "start-all" }; using var worker = new DeploymentWorker(store, targets, host, clock, NullLogger<DeploymentWorker>.Instance);
        await jobs.EnqueueAsync(project.Id, new(project.Version, "start-all"), "resume", "test");
        await worker.StartAsync(CancellationToken.None);
        try
        {
            await host.Entered.Task.WaitAsync(TimeSpan.FromSeconds(6), TestContext.Current.CancellationToken);
            project = await GetAsync(project.Id);
            var stop = await jobs.EnqueueAsync(project.Id, new(project.Version, action, side), "stop-again", "test");
            await UntilAsync(async () => (await store.ReadAsync(s => s.Jobs)).Single(x => x.Id == stop.Id).State == "succeeded");
            host.Release.TrySetResult();
            await UntilAsync(async () => (await store.ReadAsync(s => s.Jobs)).All(x => x.State == "succeeded"));
            var actual = await GetAsync(project.Id);
            Assert.True(actual.Back.Stopped);
            Assert.False(actual.Front.AutoDeploy || actual.Back.AutoDeploy);
            if (action == "stop-all") Assert.True(actual.ShutdownRequested && actual.Front.Stopped);
        }
        finally { host.Release.TrySetResult(); await worker.StopAsync(CancellationToken.None); }
    }
    [Fact]
    public async Task StartAllRejectsInvalidSideExternalDatabaseAndMissingBindingWithoutMutation()
    {
        var project = await AddAsync();
        var side = await Assert.ThrowsAsync<ConsoleException>(() => jobs.EnqueueAsync(project.Id, new(project.Version, "start-all", "front"), "side", "test"));
        Assert.Equal(400, side.Status);
        Bind(false);
        var external = await Assert.ThrowsAsync<ConsoleException>(() => jobs.EnqueueAsync(project.Id, new(project.Version, "start-all"), "external", "test"));
        Assert.Equal(409, external.Status);
        File.Delete(Path.Combine(options.Value.TargetsDirectory, "project.json"));
        var missing = await Assert.ThrowsAsync<ConsoleException>(() => jobs.EnqueueAsync(project.Id, new(project.Version, "start-all"), "missing", "test"));
        Assert.Equal(409, missing.Status);
        Assert.Empty(await store.ReadAsync(s => s.Jobs));
        Assert.Equal(project.Version, (await GetAsync(project.Id)).Version);
    }
    [Fact]
    public async Task SaveAndPublishIsAtomicAndIdempotentEvenWithoutTarget()
    {
        File.Delete(Path.Combine(options.Value.TargetsDirectory, "project.json"));
        var input = Input() with { DeployAfterSave = true, EnableSchedule = true };
        var first = await projects.SaveAsync(null, input, "save-deploy", "test");
        var replay = await projects.SaveAsync(null, input, "save-deploy", "test");
        Assert.Equal(first.Id, replay.Id);
        var job = Assert.Single(await store.ReadAsync(s => s.Jobs));
        Assert.Equal("prepare", job.Action); Assert.True(job.EnableSchedule); Assert.Equal("save", job.Trigger);
        Assert.False(first.Front.AutoDeploy || first.Back.AutoDeploy);
        await Assert.ThrowsAsync<ConsoleException>(() => projects.SaveAsync(first.Id, input with { Version = first.Version, Name = "changed" }, "another", "test"));
        Assert.Equal(first.Name, (await GetAsync(first.Id)).Name);
    }
    [Fact]
    public async Task SaveWorkflowPreparesThenDeploysBackAndFrontAndEnablesSchedule()
    {
        var project = await projects.SaveAsync(null, Input() with { DeployAfterSave = true, EnableSchedule = true }, "publish", "test");
        var host = new FakeHost(); using var worker = new DeploymentWorker(store, targets, host, clock, NullLogger<DeploymentWorker>.Instance);
        await worker.StartAsync(CancellationToken.None);
        try { await UntilAsync(async () => (await GetAsync(project.Id)).Front.LastCommit is not null); }
        finally { await worker.StopAsync(CancellationToken.None); }
        Assert.Collection(host.Requests, x => Assert.Equal("prepare", x.Action), x => Assert.Equal("back", x.Side), x => Assert.Equal("front", x.Side));
        Assert.Equal(project.RepositoryPath, host.Requests[0].Setup?.RepositoryPath);
        var actual = await GetAsync(project.Id);
        Assert.True(actual.Front.AutoDeploy && actual.Back.AutoDeploy);
        Assert.Equal(Now.AddSeconds(60), actual.Front.NextRunAt);
    }
    [Fact]
    public async Task FailedBackendCancelsFollowingFrontendWithoutEnablingPlans()
    {
        var project = await projects.SaveAsync(null, Input() with { DeployAfterSave = true, EnableSchedule = true }, "publish", "test");
        var host = new FakeHost { FailedSide = "back" }; using var worker = new DeploymentWorker(store, targets, host, clock, NullLogger<DeploymentWorker>.Instance);
        await worker.StartAsync(CancellationToken.None);
        try { await UntilAsync(async () => (await store.ReadAsync(s => s.Jobs)).Any(x => x.State == "cancelled")); }
        finally { await worker.StopAsync(CancellationToken.None); }
        Assert.DoesNotContain(host.Requests, x => x.Side == "front");
        Assert.False((await GetAsync(project.Id)).Back.AutoDeploy);
    }
    [Fact]
    public async Task SaveWorkflowKeepsStoppedSideStopped()
    {
        var project = await AddAsync();
        await store.WriteAsync(s => { ProjectService.Find(s, project.Id).Front.Stopped = true; return true; });
        await projects.SaveAsync(project.Id, Input() with { Version = project.Version, DeployAfterSave = true, EnableSchedule = true }, "publish", "test");
        var host = new FakeHost(); using var worker = new DeploymentWorker(store, targets, host, clock, NullLogger<DeploymentWorker>.Instance);
        await worker.StartAsync(CancellationToken.None);
        try { await UntilAsync(async () => (await GetAsync(project.Id)).Back.LastCommit is not null); }
        finally { await worker.StopAsync(CancellationToken.None); }
        Assert.DoesNotContain(host.Requests, x => x.Side == "front");
        Assert.True((await GetAsync(project.Id)).Front.Stopped);
        Assert.False((await GetAsync(project.Id)).Front.AutoDeploy);
    }
    [Fact]
    public async Task StopAllCancelsUnboundPreparationAndExplicitRetryResumes()
    {
        File.Delete(Path.Combine(options.Value.TargetsDirectory, "project.json"));
        var project = await projects.SaveAsync(null, Input() with { DeployAfterSave = true }, "publish", "test");
        await jobs.EnqueueAsync(project.Id, new(project.Version, "stop-all"), "stop", "test");
        Assert.Equal("cancelled", (await store.ReadAsync(s => s.Jobs))[0].State);
        project = await GetAsync(project.Id);
        await Assert.ThrowsAsync<ConsoleException>(() => projects.SaveAsync(project.Id, Input() with { Version = project.Version, DeployAfterSave = true }, "not-resume", "test"));
        await store.WriteAsync(s => { s.Jobs.Single(x => x.Action == "stop-all").State = "succeeded"; return true; });
        var retry = await jobs.EnqueueAsync(project.Id, new(project.Version, "publish"), "retry", "test");
        Assert.Equal("prepare", retry.Action);
        Assert.False((await GetAsync(project.Id)).ShutdownRequested);
    }
    [Fact]
    public async Task StopDuringPreparationPreventsDeployQueue()
    {
        var project = await projects.SaveAsync(null, Input() with { DeployAfterSave = true, EnableSchedule = true }, "publish", "test");
        var host = new PausingHost(); using var worker = new DeploymentWorker(store, targets, host, clock, NullLogger<DeploymentWorker>.Instance);
        await worker.StartAsync(CancellationToken.None);
        try
        {
            await host.Entered.Task.WaitAsync(TimeSpan.FromSeconds(6), TestContext.Current.CancellationToken);
            await jobs.EnqueueAsync(project.Id, new(project.Version, "stop-all"), "stop", "test");
            host.Release.TrySetResult();
            await UntilAsync(async () => (await store.ReadAsync(s => s.Jobs)).All(x => x.State == "succeeded"));
            Assert.DoesNotContain(await store.ReadAsync(s => s.Jobs), x => x.Action == "deploy");
        }
        finally { host.Release.TrySetResult(); await worker.StopAsync(CancellationToken.None); }
    }
    private sealed class PausingHost : IHostControl
    {
        public string PausedAction { get; init; } = "prepare";
        public TaskCompletionSource Entered { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public TaskCompletionSource Release { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public async Task<HostResult> RunAsync(HostRequest request, Func<string, Task>? progress = null)
        { if (request.Action == PausedAction) { Entered.TrySetResult(); await Release.Task; } return new() { Ok = true, Message = "test" }; }
    }
    public void Dispose() { store.Dispose(); Directory.Delete(directory, true); }
    private sealed class FixedClock : TimeProvider { public override DateTimeOffset GetUtcNow() => Now; }

    [Fact]
    public async Task DeleteRequiresShutdownExactConfirmationAndIdleProject()
    {
        var project = await AddAsync();
        var fingerprint = new string('a', 64);
        await Assert.ThrowsAsync<ConsoleException>(() => jobs.EnqueueAsync(project.Id, new(project.Version, "delete", ConfirmationSlug: project.Slug, DeletionFingerprint: fingerprint), "delete-running", "test"));
        await jobs.EnqueueAsync(project.Id, new(project.Version, "stop-all"), "stop-delete", "test");
        project = await GetAsync(project.Id);
        await Assert.ThrowsAsync<ConsoleException>(() => jobs.EnqueueAsync(project.Id, new(project.Version, "delete", ConfirmationSlug: "wrong", DeletionFingerprint: fingerprint), "delete-wrong", "test"));
        await Assert.ThrowsAsync<ConsoleException>(() => jobs.EnqueueAsync(project.Id, new(project.Version, "delete", ConfirmationSlug: project.Slug, DeletionFingerprint: fingerprint), "delete-busy", "test"));
        await store.WriteAsync(s => { s.Jobs.ForEach(x => x.State = "succeeded"); return true; });
        var input = new JobInput(project.Version, "delete", ConfirmationSlug: project.Slug, DeletionFingerprint: fingerprint);
        var job = await jobs.EnqueueAsync(project.Id, input, "delete-confirmed", "test");
        Assert.Equal(job.Id, (await jobs.EnqueueAsync(project.Id, input, "delete-confirmed", "test")).Id);
        Assert.Equal(fingerprint, job.DeletionFingerprint);
        Assert.True(job.BackupBeforeDelete);
        Assert.True((await GetAsync(project.Id)).DeletionRequested);
    }

    [Fact]
    public async Task DeleteFailureLocksOtherActionsAndKeepsCredentialsAndHistory()
    {
        var project = await AddAsync();
        await store.WriteAsync(s => { var p = ProjectService.Find(s, project.Id); p.ShutdownRequested = p.Front.Stopped = p.Back.Stopped = true; s.GitSecrets[p.Id] = "saved-ciphertext"; return true; });
        var job = await jobs.EnqueueAsync(project.Id, new(project.Version, "delete", ConfirmationSlug: project.Slug, DeletionFingerprint: new string('b', 64)), "delete-failed", "test");
        var host = new FakeHost { FailedSide = "" }; using var worker = new DeploymentWorker(store, targets, host, clock, NullLogger<DeploymentWorker>.Instance);
        await worker.StartAsync(CancellationToken.None);
        try
        {
            await UntilAsync(async () => await store.ReadAsync(s => s.Jobs.Single(x => x.Id == job.Id).State == "failed"));
            project = await GetAsync(project.Id);
            Assert.True(project.DeletionRequested);
            Assert.True(await store.ReadAsync(s => s.GitSecrets.ContainsKey(project.Id)));
            await Assert.ThrowsAsync<ConsoleException>(() => jobs.EnqueueAsync(project.Id, new(project.Version, "start-all"), "unsafe-start", "test"));
            await Assert.ThrowsAsync<ConsoleException>(() => projects.ScheduleAsync(project.Id, "front", new(project.Version, false, 3600), "unsafe-plan", "test"));
            await Assert.ThrowsAsync<ConsoleException>(() => projects.SaveAsync(project.Id, Input() with { Version = project.Version }, "unsafe-edit", "test"));
        }
        finally { await worker.StopAsync(CancellationToken.None); }
    }

    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public async Task SuccessfulDeleteRemovesProjectAndGitSecretButRetainsNamedJobAndAudit(bool backupBeforeDelete)
    {
        var project = await AddAsync();
        await store.WriteAsync(s => { var p = ProjectService.Find(s, project.Id); p.ShutdownRequested = p.Front.Stopped = p.Back.Stopped = true; s.GitSecrets[p.Id] = "saved-ciphertext"; return true; });
        var input = new JobInput(project.Version, "delete", ConfirmationSlug: project.Slug, DeletionFingerprint: new string('c', 64), BackupBeforeDelete: backupBeforeDelete, ConfirmWithoutBackup: !backupBeforeDelete);
        var job = await jobs.EnqueueAsync(project.Id, input, "delete-once", "test");
        Assert.Equal(backupBeforeDelete, job.BackupBeforeDelete);
        await Assert.ThrowsAsync<ConsoleException>(() => jobs.EnqueueAsync(project.Id, input with { BackupBeforeDelete = !backupBeforeDelete, ConfirmWithoutBackup = true }, "delete-once", "test"));
        var host = new FakeHost(); using var worker = new DeploymentWorker(store, targets, host, clock, NullLogger<DeploymentWorker>.Instance);
        await worker.StartAsync(CancellationToken.None);
        try
        {
            await UntilAsync(async () => await store.ReadAsync(s => !s.Projects.Any(p => p.Id == project.Id)));
            Assert.False(await store.ReadAsync(s => s.GitSecrets.ContainsKey(project.Id)));
            Assert.Equal(project.Name, (await store.ReadAsync(s => s.Jobs.Single(x => x.Id == job.Id))).ProjectName);
            Assert.True(await store.ReadAsync(s => s.Audit.Any(x => x.Action == "project.deleted" && x.ProjectId == project.Id)));
            Assert.Equal(job.Id, (await jobs.EnqueueAsync(project.Id, input, "delete-once", "test")).Id);
            Assert.Single(host.Requests);
            Assert.Equal(backupBeforeDelete, host.Requests[0].BackupBeforeDelete);
            Assert.Equal(!backupBeforeDelete, host.Requests[0].ConfirmWithoutBackup);
            Assert.True(await store.ReadAsync(s => s.Audit.Where(x => x.ProjectId == project.Id && x.Action.StartsWith("project.delet", StringComparison.Ordinal)).All(x => x.Detail.Contains($"backupBeforeDelete={backupBeforeDelete}"))));
        }
        finally { await worker.StopAsync(CancellationToken.None); }
    }

    [Fact]
    public async Task DeleteWithoutBackupRequiresSeparateConsentAndDoesNotQueueOnRejection()
    {
        var project = await AddAsync();
        var error = await Assert.ThrowsAsync<ConsoleException>(() => jobs.EnqueueAsync(project.Id,
            new(project.Version, "delete", ConfirmationSlug: project.Slug, DeletionFingerprint: new string('d', 64), BackupBeforeDelete: false), "missing-consent", "test"));
        Assert.Contains("明确确认不备份", error.Message);
        Assert.False((await GetAsync(project.Id)).DeletionRequested);
        Assert.Empty(await store.ReadAsync(s => s.Jobs));
    }

    [Fact]
    public void OldDeleteRequestsAndQueuedJobsDefaultToBackup()
    {
        Assert.True(JsonSerializer.Deserialize<JobInput>("{\"version\":1,\"action\":\"delete\"}", StateStore.Json)!.BackupBeforeDelete);
        Assert.True(JsonSerializer.Deserialize<OperationJob>("{\"action\":\"delete\"}", StateStore.Json)!.BackupBeforeDelete);
        Assert.False(JsonSerializer.Deserialize<JobInput>("{\"version\":1,\"action\":\"delete\",\"backupBeforeDelete\":false,\"confirmWithoutBackup\":true}", StateStore.Json)!.BackupBeforeDelete);
    }

    private sealed class FakeHost : IHostControl
    {
        public string? FailedSide { get; init; }
        public bool NoChanges { get; init; }
        public List<HostRequest> Requests { get; } = [];
        public Task<HostResult> RunAsync(HostRequest request, Func<string, Task>? progress = null) { Requests.Add(request); return Task.FromResult(new HostResult { Ok = request.Side != FailedSide, NoChanges = NoChanges, Message = "test", Commit = new string('a', 40) }); }
    }
}
