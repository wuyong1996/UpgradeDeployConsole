using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace DeployConsole;

public sealed class ProjectService(StateStore store, TargetCatalog catalog, TimeProvider clock, GitCredentials credentials, BranchProofStore proofs)
{
    public static readonly int[] Periods = [0, 60, 300, 900, 1800, 3600, 86400];
    public static DeploymentProject Find(ConsoleState state, Guid id) => state.Projects.SingleOrDefault(x => x.Id == id) ?? throw new ConsoleException(404, "项目不存在");
    public static void Version(DeploymentProject project, long version) { if (project.Version != version) throw new ConsoleException(409, "配置已变化，请刷新后重试"); }
    public static void Editable(DeploymentProject project) { if (project.DeletionRequested) throw new ConsoleException(409, "项目已进入删除流程，不能再修改或启动，请查看删除记录"); }
    public static string Key(HttpContext context)
    {
        var key = context.Request.Headers["Idempotency-Key"].ToString();
        if (!Guid.TryParse(key, out _)) throw new ConsoleException(400, "缺少有效操作标识");
        return key;
    }
    public static string Hash(object value) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(value, StateStore.Json))));
    public static bool Match(string value, string pattern) => Regex.IsMatch(value, pattern, RegexOptions.CultureInvariant, TimeSpan.FromSeconds(1));
    public static bool Overlaps(string left, string right) => left == right || left.StartsWith(right + "/", StringComparison.Ordinal) || right.StartsWith(left + "/", StringComparison.Ordinal);
    public static bool ValidBranch(string branch) => Match(branch, "^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$") && !branch.Contains("..", StringComparison.Ordinal) && !branch.Contains("//", StringComparison.Ordinal) && !branch.EndsWith('/') && !branch.EndsWith('.') && !branch.EndsWith(".lock", StringComparison.Ordinal);
    public static void ValidateRepository(string repository)
    {
        if (string.IsNullOrEmpty(repository) || repository.Any(char.IsControl) || repository.Length > 2000 || !Uri.TryCreate(repository, UriKind.Absolute, out var uri) || uri.Scheme is not ("https" or "ssh") || uri.AbsolutePath.Length < 2 || !string.IsNullOrEmpty(uri.Query) || !string.IsNullOrEmpty(uri.Fragment) || (uri.Scheme == "https" && (uri.UserInfo.Length > 0 || uri.Port != 443)) || (uri.Scheme == "ssh" && (uri.UserInfo != "git" || uri.Port is not (-1 or 22)))) throw new ConsoleException(400, "Git 地址须使用标准 HTTPS 或 SSH 端口，不能包含密码或 Token");
    }

    public static void Validate(ProjectInput input)
    {
        if (new[] { input.Slug, input.Name, input.Environment, input.Repository, input.Branch, input.FrontPath, input.BackPath, input.RepositoryPath }.Any(string.IsNullOrWhiteSpace)) throw new ConsoleException(400, "请填写完整的项目配置");
        if (!Match(input.Slug, "^[a-z][a-z0-9-]{0,63}$") || string.IsNullOrWhiteSpace(input.Name) || input.Name.Length > 100 || input.Environment is not ("test" or "production")) throw new ConsoleException(400, "项目名称、标识或环境无效");
        if (!ValidBranch(input.Branch)) throw new ConsoleException(400, "分支名称无效");
        ValidateRepository(input.Repository);
        var paths = new[] { input.FrontPath, input.BackPath, input.RepositoryPath };
        if (paths.Any(x => !Match(x, "^/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)*$") || x.Split('/').Skip(1).Any(p => p is ".." or ".") || x.Length > 400) || paths.Where((path, index) => paths.Skip(index + 1).Any(other => Overlaps(path, other))).Any())
            throw new ConsoleException(400, "请使用三个不同且不含跳转片段的服务器绝对路径");
    }

    public Task<DeploymentProject> SaveAsync(Guid? id, ProjectInput input, string key, string source)
    {
        Validate(input);
        var target = catalog.Find(input.Slug);
        if (target is not null && ((target.Front is not null && target.Front.Current != input.FrontPath) || (target.Back is not null && target.Back.Current != input.BackPath) || target.RepositoryPath != input.RepositoryPath || !target.AllowedGitHosts.Contains(new Uri(input.Repository).Host, StringComparer.OrdinalIgnoreCase))) throw new ConsoleException(400, "项目路径或 Git 主机不符合服务器接入文件");
        var hash = input.GitAuth is null && input.BranchVerification is null && !input.DeployAfterSave && !input.EnableSchedule
            ? Hash(new { id, input = new { input.Version, input.Slug, input.Name, input.Environment, input.Repository, input.Branch, input.FrontPath, input.BackPath, input.RepositoryPath } })
            : credentials.Hash(new { id, input });
        return store.WriteAsync(state =>
        {
            if (state.Writes.Find(x => x.Key == key) is { } previous) { if (previous.Hash != hash) throw new ConsoleException(409, "操作标识已用于其他内容"); return Find(state, previous.ProjectId); }
            var project = id.HasValue ? Find(state, id.Value) : new DeploymentProject();
            Editable(project);
            if (id.HasValue) { Version(project, input.Version); if (project.Slug != input.Slug) throw new ConsoleException(400, "接入后不能修改项目标识"); }
            var credential = credentials.Resolve(state, id, input.Repository, input.GitAuth);
            proofs.Validate(id, input, credential);
            var credentialChanged = project.GitAuthMode != (credential is null ? "none" : "https") || project.GitUsername != (credential?.Username ?? "") || input.GitAuth?.Secret is { Length: > 0 };
            if (state.Jobs.Any(x => x.ProjectId == project.Id && x.State is "queued" or "running")) throw new ConsoleException(409, "项目有执行中的操作，请稍后修改配置");
            var requestedPaths = new[] { input.FrontPath, input.BackPath, input.RepositoryPath };
            var conflict = state.Projects.Any(x => x.Id != project.Id &&
                (x.Slug == input.Slug || new[] { x.FrontPath, x.BackPath, x.RepositoryPath }.Any(old => requestedPaths.Any(path => Overlaps(old, path)))));
            if (conflict) throw new ConsoleException(409, "项目标识或目录已被占用");
            if (project.Repository != input.Repository || project.Branch != input.Branch || credentialChanged) foreach (var plan in new[] { project.Front, project.Back }) { plan.AutoDeploy = false; plan.NextRunAt = null; }
            project.Name = input.Name.Trim(); project.Slug = input.Slug; project.Environment = input.Environment; project.Repository = input.Repository; project.Branch = input.Branch;
            project.FrontPath = input.FrontPath; project.BackPath = input.BackPath; project.RepositoryPath = input.RepositoryPath;
            project.GitAuthMode = credential is null ? "none" : "https"; project.GitUsername = credential?.Username ?? "";
            if (credential is null) state.GitSecrets.Remove(project.Id); else state.GitSecrets[project.Id] = credentials.Protect(credential);
            if (!id.HasValue) state.Projects.Add(project); else project.Version++;
            if (input.DeployAfterSave) JobService.QueuePublish(state, project, clock.GetUtcNow(), input.EnableSchedule);
            state.Writes.Add(new(key, hash, project.Id));
            state.Audit.Add(new(clock.GetUtcNow(), id.HasValue ? "project.updated" : "project.created", project.Id, source, $"version={project.Version}; source/config saved"));
            return project;
        });
    }

    public Task<DeploymentProject> ScheduleAsync(Guid id, string side, PlanInput input, string key, string source)
    {
        if (!Periods.Contains(input.PeriodSeconds)) throw new ConsoleException(400, "检查周期无效");
        var hash = Hash(new { id, side, input });
        return store.WriteAsync(state =>
        {
            var project = Find(state, id);
            if (state.Writes.Find(x => x.Key == key) is { } previous) { if (previous.Hash != hash) throw new ConsoleException(409, "操作标识冲突"); return project; }
            Version(project, input.Version);
            Editable(project);
            var plan = project.Plan(side);
            var target = catalog.Find(project.Slug);
            if (input.AutoDeploy && (plan.Stopped || project.ShutdownRequested || target is null || (side == "front" ? target.Front : target.Back) is null)) throw new ConsoleException(409, "请先接入并启动对应服务");
            if (plan.AutoDeploy != input.AutoDeploy || plan.PeriodSeconds != input.PeriodSeconds)
            {
                plan.AutoDeploy = input.AutoDeploy; plan.PeriodSeconds = input.PeriodSeconds;
                plan.NextRunAt = input.AutoDeploy && input.PeriodSeconds > 0 ? clock.GetUtcNow().AddSeconds(input.PeriodSeconds) : null;
            }
            project.Version++;
            state.Writes.Add(new(key, hash, id));
            state.Audit.Add(new(clock.GetUtcNow(), "schedule.updated", id, source, $"{side}; enabled={plan.AutoDeploy}; period={plan.PeriodSeconds}"));
            return project;
        });
    }
}
