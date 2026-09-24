namespace DeployConsole;

public sealed class JobService(StateStore store, TargetCatalog catalog, TimeProvider clock)
{
    public static readonly string[] Sides = ["front", "back"];
    public static OperationJob QueuePublish(ConsoleState state, DeploymentProject project, DateTimeOffset now, bool enableSchedule)
    {
        if (project.ShutdownRequested) throw new ConsoleException(409, "项目已总关停，请点击“总开始”恢复数据库和前后端服务后发布");
        if (project.Front.Stopped && project.Back.Stopped) throw new ConsoleException(409, "前后端均已停止，请先恢复服务后发布");
        if (state.Jobs.Any(x => x.ProjectId == project.Id && x.State is "queued" or "running")) throw new ConsoleException(409, "项目已有操作正在执行");
        foreach (var plan in new[] { project.Front, project.Back }) { plan.AutoDeploy = false; plan.NextRunAt = null; }
        var job = Create(project, "prepare", "", now); job.Trigger = "save"; job.EnableSchedule = enableSchedule;
        job.Step = "等待拉取代码并准备发布";
        state.Jobs.Add(job);
        return job;
    }
    public Task<OperationJob> EnqueueAsync(Guid id, JobInput input, string key, string source)
    {
        if (input.Action is not ("publish" or "check" or "deploy" or "rollback" or "start" or "start-all" or "stop" or "restart" or "port" or "stop-all" or "delete")) throw new ConsoleException(400, "操作无效");
        if (input.Action is not ("publish" or "check" or "stop-all" or "start-all" or "delete") && input.Side is not ("front" or "back" or "database")) throw new ConsoleException(400, "服务类型无效");
        if (input.Action == "delete" && (input.Side != "" || input.DeletionFingerprint is null || !ProjectService.Match(input.DeletionFingerprint, "\\A[a-f0-9]{64}\\z"))) throw new ConsoleException(400, "请先加载删除预览并确认项目标识");
        if (input.Action == "delete" && !input.BackupBeforeDelete && !input.ConfirmWithoutBackup) throw new ConsoleException(400, "请明确确认不备份删除，本次不会生成恢复备份");
        if (input.Action == "start-all" && input.Side != "") throw new ConsoleException(400, "总开始不能指定单个服务");
        if (input.Side == "database" && input.Action != "start") throw new ConsoleException(400, "数据库只允许恢复启动或纳入总关停");
        if (input.Action == "port" && input.Port is not (> 0 and <= 65535)) throw new ConsoleException(400, "端口必须为 1–65535");
        if (input.Commit is not null) throw new ConsoleException(400, "当前仅支持回退上一应用版本，不能指定提交");
        var hash = ProjectService.Hash(new { id, input });
        return store.WriteAsync(state =>
        {
            if (state.Jobs.Find(x => x.IdempotencyKey == key) is { } previous) { if (previous.RequestHash != hash) throw new ConsoleException(409, "操作标识冲突"); return previous; }
            var project = ProjectService.Find(state, id);
            ProjectService.Version(project, input.Version);
            if (project.DeletionRequested && input.Action != "delete") throw new ConsoleException(409, "项目已进入删除流程，请查看删除记录并重试清理");
            if (input.Action == "delete")
            {
                if (!project.ShutdownRequested || !project.Front.Stopped || !project.Back.Stopped) throw new ConsoleException(409, "请先总关停项目后再删除");
                if (input.ConfirmationSlug != project.Slug) throw new ConsoleException(400, "项目标识不匹配，未删除");
                if (state.Jobs.Any(x => x.ProjectId == id && x.State is "queued" or "running")) throw new ConsoleException(409, "项目操作尚未结束，请稍后删除");
                project.DeletionRequested = true;
                project.Version++;
                var deletion = Create(project, "delete", "", clock.GetUtcNow());
                deletion.DeletionFingerprint = input.DeletionFingerprint; deletion.IdempotencyKey = key; deletion.RequestHash = hash;
                deletion.BackupBeforeDelete = input.BackupBeforeDelete;
                state.Jobs.Add(deletion);
                state.Audit.Add(new(clock.GetUtcNow(), "project.deletion-confirmed", id, source, $"{deletion.Id}; {project.Slug}; backupBeforeDelete={input.BackupBeforeDelete}"));
                return deletion;
            }
            if (input.Action == "publish")
            {
                if (catalog.Find(project.Slug) is null && project.ShutdownRequested)
                { project.ShutdownRequested = false; project.Front.Stopped = false; project.Back.Stopped = false; project.Version++; }
                var enable = state.Jobs.Where(x => x.ProjectId == id && x.Action == "prepare").OrderByDescending(x => x.CreatedAt).FirstOrDefault()?.EnableSchedule ?? false;
                var publish = QueuePublish(state, project, clock.GetUtcNow(), enable);
                publish.IdempotencyKey = key; publish.RequestHash = hash;
                state.Audit.Add(new(clock.GetUtcNow(), "job.queued", id, source, $"{publish.Id}; prepare"));
                return publish;
            }
            var target = catalog.Find(project.Slug);
            if (target is null && (input.Action != "stop-all" || !state.Jobs.Any(x => x.ProjectId == id && x.Action == "prepare" && x.State is "queued" or "running"))) throw new ConsoleException(409, "请先执行项目发布完成自动接入");
            if (input.Side is "front" or "back" && (input.Side == "front" ? target?.Front : target?.Back) is null) throw new ConsoleException(409, "该服务未配置");
            if ((input.Action is "stop-all" or "start-all" || input.Side == "database") && target is not null && target.DatabaseKind != "none" && !target.DatabaseManaged) throw new ConsoleException(409, "数据库尚未接管启停，整项目启停未执行");
            if (input.Action is "deploy" or "rollback" && (project.ShutdownRequested || project.Plan(input.Side).Stopped)) throw new ConsoleException(409, "请先启动对应服务");
            if (state.Jobs.Any(x => x.ProjectId == id && x.State is "queued" or "running") && input.Action is not ("stop" or "stop-all")) throw new ConsoleException(409, "项目已有操作正在执行");
            if (input.Action == "start-all")
            {
                foreach (var plan in new[] { project.Front, project.Back }) { plan.AutoDeploy = false; plan.NextRunAt = null; }
                project.Version++;
            }
            if (input.Action is "stop" or "stop-all")
            {
                foreach (var side in input.Action == "stop-all" ? Sides : [input.Side])
                {
                    var plan = project.Plan(side); plan.Stopped = true; plan.AutoDeploy = false; plan.NextRunAt = null;
                }
                if (input.Action == "stop-all") project.ShutdownRequested = true;
                foreach (var queued in state.Jobs.Where(x => x.ProjectId == id && x.State == "queued" && (input.Action == "stop-all" || x.Side == input.Side)))
                { queued.State = "cancelled"; queued.Step = "关停请求取消未开始操作"; queued.FinishedAt = clock.GetUtcNow(); }
                project.Version++;
            }
            var job = Create(project, input.Action, input.Side, clock.GetUtcNow());
            job.Port = input.Port; job.Commit = input.Commit; job.IdempotencyKey = key; job.RequestHash = hash;
            state.Jobs.Add(job);
            state.Audit.Add(new(clock.GetUtcNow(), "job.queued", id, source, $"{job.Id}; {input.Action}; {input.Side}; port={input.Port}"));
            return job;
        });
    }
    public static OperationJob Create(DeploymentProject project, string action, string side, DateTimeOffset now) => new()
    {
        ProjectId = project.Id, Action = action, Side = side, CreatedAt = now,
        ProjectName = project.Name,
        ProjectVersion = project.Version, Repository = project.Repository, Branch = project.Branch,
    };
}

public sealed class DeploymentWorker(StateStore store, TargetCatalog targets, IHostControl host, TimeProvider clock, ILogger<DeploymentWorker> logger) : BackgroundService
{
    private static readonly string[] PublishOrder = ["back", "front"];
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        await store.WriteAsync(state =>
        {
            foreach (var job in state.Jobs.Where(x => x.State == "running"))
            { job.State = "interrupted"; job.Step = "面板重启，请核对服务器实际状态后重试"; job.FinishedAt = clock.GetUtcNow(); }
            return true;
        });
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                var job = await TakeAsync(false);
                if (job is not null)
                {
                    var execution = ExecuteJobAsync(job);
                    while (!execution.IsCompleted)
                    {
                        if (await TakeAsync(true) is { } stop) await ExecuteJobAsync(stop);
                        else await Task.WhenAny(execution, Task.Delay(TimeSpan.FromSeconds(1), CancellationToken.None));
                    }
                    await execution;
                }
                else await Task.Delay(TimeSpan.FromSeconds(2), clock, stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
            catch (Exception ex) { ConsoleLog.Scheduler(logger, ex.GetType().Name); await Task.Delay(TimeSpan.FromSeconds(5), CancellationToken.None); }
        }
    }

    private Task<OperationJob?> TakeAsync(bool stopOnly) => store.WriteAsync(state =>
    {
        var now = clock.GetUtcNow();
        foreach (var project in state.Projects.Where(x => !stopOnly && !x.ShutdownRequested && !x.DeletionRequested))
        {
            var binding = targets.Find(project.Slug);
            if (binding is null) continue;
            foreach (var side in JobService.Sides)
            {
                var plan = project.Plan(side);
                if (!plan.AutoDeploy || plan.Stopped || plan.PeriodSeconds == 0 || plan.NextRunAt > now || plan.NextRunAt is null || (side == "front" ? binding.Front : binding.Back) is null) continue;
                if (state.Jobs.Any(x => x.ProjectId == project.Id && (x.Side == side || x.Action is "prepare" or "start-all") && x.State is "queued" or "running")) continue;
                var job = JobService.Create(project, "deploy", side, now); job.Trigger = "schedule"; state.Jobs.Add(job);
                var elapsed = Math.Max(0, (now - plan.NextRunAt.Value).TotalSeconds);
                plan.NextRunAt = plan.NextRunAt.Value.AddSeconds((Math.Floor(elapsed / plan.PeriodSeconds) + 1) * plan.PeriodSeconds);
            }
        }
        var next = state.Jobs.Where(x => x.State == "queued" && (!stopOnly || x.Action is "stop" or "stop-all")).OrderBy(x => x.Action == "stop-all" ? 0 : x.Action == "stop" ? 1 : 2).ThenBy(x => x.CreatedAt).FirstOrDefault();
        if (next is not null) { next.State = "running"; next.Step = "校验接入配置"; }
        return next;
    });

    private async Task ExecuteJobAsync(OperationJob job)
    {
        var project = await store.ReadAsync(s => ProjectService.Find(s, job.ProjectId));
        HostResult result;
        try
        {
            if (project.Repository != job.Repository || project.Branch != job.Branch || (job.Action == "start-all" && project.Version != job.ProjectVersion) || (job.Action == "prepare" && project.ShutdownRequested) || (job.Action is "deploy" or "rollback" && (project.ShutdownRequested || project.Plan(job.Side).Stopped)))
                result = new() { Ok = false, Message = "项目来源或目标状态已变化，本次操作未执行" };
            else result = await host.RunAsync(new(project.Slug, job.Action, job.Side, project.Repository, project.Branch, job.Id.ToString(), job.Port, job.Commit, Setup: job.Action == "prepare" ? new(project.FrontPath, project.BackPath, project.RepositoryPath) : null, RequestedAtUnixMs: job.Action == "start-all" ? job.CreatedAt.ToUnixTimeMilliseconds() : null, DeletionFingerprint: job.DeletionFingerprint, BackupBeforeDelete: job.BackupBeforeDelete, ConfirmWithoutBackup: job.Action == "delete" && !job.BackupBeforeDelete), step => store.WriteAsync(s =>
            {
                var current = s.Jobs.Single(x => x.Id == job.Id); current.Step = step;
                if (current.Events.Count < 500) current.Events.Add(new(clock.GetUtcNow(), step));
                return true;
            }));
        }
        catch (Exception ex) { ConsoleLog.Operation(logger, job.Id, ex.GetType().Name); result = new() { Ok = false, Message = "执行失败，请检查服务器接入文件与操作记录" }; }
        if (job.Action == "prepare" && result.Ok && targets.Find(project.Slug) is null)
            result = new() { Ok = false, Message = "首次接入没有生成服务配置，请检查服务器自动接入入口后重试" };

        await store.WriteAsync(state =>
        {
            var current = state.Jobs.Single(x => x.Id == job.Id);
            current.State = result.Ok ? "succeeded" : "failed"; current.Result = result; current.Step = result.Message; current.FinishedAt = clock.GetUtcNow();
            current.Events.Add(new(clock.GetUtcNow(), result.Message));
            var actual = ProjectService.Find(state, job.ProjectId);
            if (job.Action == "delete" && result.Ok)
            {
                state.GitSecrets.Remove(actual.Id);
                state.Projects.Remove(actual);
                state.Audit.Add(new(clock.GetUtcNow(), "project.deleted", job.ProjectId, job.Trigger, $"{actual.Slug}; {job.Id}; backupBeforeDelete={job.BackupBeforeDelete}; backup={result.BackupDirectory}"));
                return true;
            }
            if (job.Action == "start-all" && result.Ok && actual.Version == job.ProjectVersion)
            {
                actual.ShutdownRequested = false;
                foreach (var side in JobService.Sides)
                {
                    var plan = actual.Plan(side); plan.Stopped = false; plan.AutoDeploy = false; plan.NextRunAt = null;
                    plan.LastCommit = (side == "front" ? result.Front : result.Back)?.Commit ?? plan.LastCommit;
                }
                actual.Version++;
            }
            if (job.Action == "prepare" && result.Ok && !actual.ShutdownRequested)
            {
                var binding = targets.Find(actual.Slug);
                foreach (var side in PublishOrder)
                {
                    if ((side == "front" ? binding?.Front : binding?.Back) is null || actual.Plan(side).Stopped) continue;
                    var deploy = JobService.Create(actual, "deploy", side, clock.GetUtcNow());
                    deploy.Trigger = "save"; deploy.EnableSchedule = job.EnableSchedule;
                    state.Jobs.Add(deploy);
                }
            }
            if (!result.Ok && job.Trigger == "save")
                foreach (var pending in state.Jobs.Where(x => x.ProjectId == job.ProjectId && x.Trigger == "save" && x.State == "queued"))
                { pending.State = "cancelled"; pending.Step = "前置发布失败，请修复后重新发布项目"; pending.FinishedAt = clock.GetUtcNow(); }
            if (job.Side is "front" or "back")
            {
                var plan = actual.Plan(job.Side);
                if (job.Action is "deploy" or "check" or "rollback")
                {
                    plan.LastCheckAt = clock.GetUtcNow(); plan.LastResult = result.Message;
                    if (result.Ok && job.Action is "deploy" or "rollback") plan.LastCommit = result.Commit;
                    if (result.Ok && job.Action == "deploy" && job.EnableSchedule && actual.Version == job.ProjectVersion && !plan.Stopped && !actual.ShutdownRequested)
                    { plan.AutoDeploy = plan.PeriodSeconds > 0; plan.NextRunAt = plan.AutoDeploy ? clock.GetUtcNow().AddSeconds(plan.PeriodSeconds) : null; }
                    if (!result.Ok && job.Trigger == "schedule" && plan.AutoDeploy && !plan.Stopped && plan.PeriodSeconds > 0 && !actual.ShutdownRequested) plan.NextRunAt = clock.GetUtcNow().AddSeconds(Math.Max(600, plan.PeriodSeconds));
                }
                if (result.Ok && job.Action == "start" && !state.Jobs.Any(x => x.ProjectId == job.ProjectId && x.State == "queued" && (x.Action == "stop-all" || x.Action == "stop" && x.Side == job.Side)))
                { plan.Stopped = false; actual.ShutdownRequested = false; actual.Version++; }
            }
            // Keep the latest check status, but do not retain idle polling as deployment history.
            if (job.Trigger == "schedule" && job.Action == "deploy" && result.Ok && result.NoChanges)
                state.Jobs.Remove(current);
            else
                state.Audit.Add(new(clock.GetUtcNow(), "job." + current.State, job.ProjectId, job.Trigger, $"{job.Id}; {job.Action}; {job.Side}"));
            return true;
        });
    }
}
