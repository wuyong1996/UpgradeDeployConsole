using System.Net;
using System.Threading.RateLimiting;
using DeployConsole;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.Extensions.Options;

var builder = WebApplication.CreateBuilder(args);
builder.Services.Configure<ConsoleOptions>(builder.Configuration.GetSection("Console"));
builder.Services.Configure<HostOptions>(x => x.ShutdownTimeout = TimeSpan.FromHours(1));
builder.Services.AddSingleton(TimeProvider.System);
builder.Services.AddSingleton<StateStore>();
builder.Services.AddSingleton<ConsoleAuth>();
builder.Services.AddSingleton<TargetCatalog>();
builder.Services.AddSingleton<ProjectService>();
builder.Services.AddSingleton<GitCredentials>();
builder.Services.AddSingleton<BranchProofStore>();
builder.Services.AddSingleton<IGitBranchReader, GitBranchReader>();
builder.Services.AddSingleton<GitRepositoryService>();
builder.Services.AddSingleton<JobService>();
builder.Services.AddSingleton<IHostControl, LinuxHostControl>();
builder.Services.AddHostedService<DeploymentWorker>();
builder.Services.AddOpenApi();
builder.Services.AddRateLimiter(options =>
{
    options.RejectionStatusCode = 429;
    options.AddPolicy("login", context => RateLimitPartition.GetFixedWindowLimiter(context.Connection.RemoteIpAddress?.ToString() ?? "unknown", _ => new FixedWindowRateLimiterOptions { PermitLimit = 5, Window = TimeSpan.FromMinutes(1), QueueLimit = 0 }));
});
builder.WebHost.ConfigureKestrel(x => x.Limits.MaxRequestBodySize = 16384);
var app = builder.Build();
app.Use(async (context, next) =>
{
    context.Response.Headers["X-Content-Type-Options"] = "nosniff";
    context.Response.Headers["Referrer-Policy"] = "no-referrer";
    context.Response.Headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'";
    try
    {
        var options = context.RequestServices.GetRequiredService<IOptions<ConsoleOptions>>().Value;
        if (options.AllowLoopbackHttp && (context.Connection.RemoteIpAddress is null || !IPAddress.IsLoopback(context.Connection.RemoteIpAddress))) throw new ConsoleException(403, "本地模式仅允许回环访问");
        if (context.Request.Path.StartsWithSegments("/api") || context.Request.Path.StartsWithSegments("/openapi"))
        {
            context.Response.Headers.CacheControl = "no-store";
            var write = !HttpMethods.IsGet(context.Request.Method) && !HttpMethods.IsHead(context.Request.Method);
            if (write && context.Request.Headers.TryGetValue("Origin", out var origin) && (!Uri.TryCreate(origin, UriKind.Absolute, out var uri) || uri.Authority != context.Request.Host.Value || (!options.AllowLoopbackHttp && uri.Scheme != "https"))) throw new ConsoleException(403, "请求来源不允许");
            if (context.Request.Path != "/api/v1/auth/login") await context.RequestServices.GetRequiredService<ConsoleAuth>().ValidateAsync(context, write);
        }
        await next(context);
    }
    catch (ConsoleException fault) { context.Response.StatusCode = fault.Status; await context.Response.WriteAsJsonAsync(new { message = fault.Message, traceId = context.TraceIdentifier }); }
    catch (BadHttpRequestException) { context.Response.StatusCode = 400; await context.Response.WriteAsJsonAsync(new { message = "请求格式无效" }); }
    catch (Exception ex) { ConsoleLog.Request(app.Logger, ex.GetType().Name); context.Response.StatusCode = 500; await context.Response.WriteAsJsonAsync(new { message = "服务暂时不可用，请稍后重试", traceId = context.TraceIdentifier }); }
});
app.UseRateLimiter();
app.UseDefaultFiles();
app.UseStaticFiles();
app.MapOpenApi();
app.MapGet("/health/live", () => Results.Ok(new { status = "healthy" }));
app.MapPost("/api/v1/auth/login", async (LoginRequest input, HttpContext context, ConsoleAuth auth, StateStore store, TimeProvider clock) =>
{
    try
    {
        var result = await auth.LoginAsync(context, input.Password ?? "");
        await store.WriteAsync(s => { s.Audit.Add(new(clock.GetUtcNow(), "login.succeeded", null, context.Connection.RemoteIpAddress?.ToString() ?? "unknown", "")); return true; });
        return Results.Ok(result);
    }
    catch (ConsoleException)
    {
        await store.WriteAsync(s => { s.Audit.Add(new(clock.GetUtcNow(), "login.failed", null, context.Connection.RemoteIpAddress?.ToString() ?? "unknown", "")); return true; });
        throw;
    }
}).RequireRateLimiting("login");
app.MapGet("/api/v1/auth/session", (HttpContext context, ConsoleAuth auth) => auth.ValidateAsync(context, false));
app.MapPost("/api/v1/auth/logout", (HttpContext context, ConsoleAuth auth) => { auth.Logout(context); return Results.NoContent(); });
app.MapGet("/api/v1/projects", (StateStore store) => store.ReadAsync(s => s.Projects));
app.MapGet("/api/v1/targets", (TargetCatalog targets) => Results.Ok(targets.List()));
app.MapPost("/api/v1/git/branches", (GitBranchesInput input, GitRepositoryService git, CancellationToken cancellationToken) => git.BranchesAsync(input, cancellationToken));
app.MapPost("/api/v1/projects", async (ProjectInput input, HttpContext context, ProjectService projects) => Results.Created("/api/v1/projects", await projects.SaveAsync(null, input, ProjectService.Key(context), context.Connection.RemoteIpAddress?.ToString() ?? "unknown")));
app.MapPut("/api/v1/projects/{id:guid}", (Guid id, ProjectInput input, HttpContext context, ProjectService projects) => projects.SaveAsync(id, input, ProjectService.Key(context), context.Connection.RemoteIpAddress?.ToString() ?? "unknown"));
app.MapPut("/api/v1/projects/{id:guid}/plans/{side}", (Guid id, string side, PlanInput input, HttpContext context, ProjectService projects) => projects.ScheduleAsync(id, side, input, ProjectService.Key(context), context.Connection.RemoteIpAddress?.ToString() ?? "unknown"));
app.MapGet("/api/v1/projects/{id:guid}/status", async (Guid id, StateStore store, IHostControl host, TargetCatalog targets) =>
{
    var project = await store.ReadAsync(s => ProjectService.Find(s, id));
    return targets.Find(project.Slug) is null ? new HostResult { Ok = false, Message = "项目为接入草稿，尚未绑定真实服务" } : await host.RunAsync(new(project.Slug, "status", "", project.Repository, project.Branch, Guid.NewGuid().ToString()));
});
app.MapPost("/api/v1/projects/{id:guid}/jobs", async (Guid id, JobInput input, HttpContext context, JobService jobs) => Results.Accepted("/api/v1/jobs", await jobs.EnqueueAsync(id, input, ProjectService.Key(context), context.Connection.RemoteIpAddress?.ToString() ?? "unknown")));
app.MapGet("/api/v1/projects/{id:guid}/deletion", async (Guid id, StateStore store, IHostControl host) =>
{
    var project = await store.ReadAsync(s =>
    {
        var value = ProjectService.Find(s, id);
        if (!value.ShutdownRequested || !value.Front.Stopped || !value.Back.Stopped) throw new ConsoleException(409, "请先总关停项目后再查看删除范围");
        if (s.Jobs.Any(x => x.ProjectId == id && x.State is "queued" or "running")) throw new ConsoleException(409, "项目操作尚未结束，请稍后删除");
        return value;
    });
    var result = await host.RunAsync(new(project.Slug, "delete-preview", "", project.Repository, project.Branch, Guid.NewGuid().ToString()));
    if (!result.Ok) throw new ConsoleException(409, result.Message);
    return result.Deletion;
});
app.MapGet("/api/v1/jobs", (Guid? projectId, int? page, StateStore store) => store.ReadAsync(s => new { total = s.Jobs.Count(x => !projectId.HasValue || x.ProjectId == projectId), items = s.Jobs.Where(x => !projectId.HasValue || x.ProjectId == projectId).OrderByDescending(x => x.CreatedAt).Skip((Math.Clamp(page ?? 1, 1, 100000) - 1) * 20).Take(20).ToArray() }));
app.MapGet("/api/v1/audit", (int? page, StateStore store) => store.ReadAsync(s => new { total = s.Audit.Count, items = s.Audit.AsEnumerable().Reverse().Skip((Math.Clamp(page ?? 1, 1, 100000) - 1) * 20).Take(20).ToArray() }));
app.MapGet("/api/v1/settings", (IOptions<ConsoleOptions> options) => new { passwordFile = options.Value.PasswordFile, timezone = "Asia/Shanghai", linux = OperatingSystem.IsLinux(), periods = ProjectService.Periods, localMode = options.Value.AllowLoopbackHttp });
app.MapGet("/api/v1/settings/https", async (IHostControl host) =>
{
    var result = await host.RunAsync(new("console", "https-status", "", "", "", Guid.NewGuid().ToString()));
    if (!result.Ok) throw new ConsoleException(409, result.Message);
    return result.Https;
});
app.MapPut("/api/v1/settings/https", async (HttpsInput input, IHostControl host, StateStore store, TimeProvider clock, HttpContext context) =>
{
    var validated = HttpsSettings.Validate(input);
    var result = await host.RunAsync(new("console", "https-configure", "", "", "", Guid.NewGuid().ToString(), Https: validated));
    if (!result.Ok) throw new ConsoleException(409, result.Message);
    await store.WriteAsync(s => { s.Audit.Add(new(clock.GetUtcNow(), "https.configured", null, context.Connection.RemoteIpAddress?.ToString() ?? "unknown", "HTTPS certificate verified and applied to project sites")); return true; });
    return result.Https;
});
app.MapGet("/api/v1/settings/mysql", async (IHostControl host) =>
{
    var result = await host.RunAsync(new("console", "mysql-status", "", "", "", Guid.NewGuid().ToString()));
    if (!result.Ok) throw new ConsoleException(409, result.Message);
    return result.MySql;
});
app.MapPut("/api/v1/settings/mysql", async (MySqlInput input, IHostControl host, StateStore store, TimeProvider clock, HttpContext context) =>
{
    if (string.IsNullOrEmpty(input.Username) || input.Username.Length > 32 || input.Username.Any(c => !char.IsAsciiLetterOrDigit(c) && c is not ('_' or '-'))
        || input.Port is not (> 0 and <= 65535) || string.IsNullOrEmpty(input.Password) || input.Password.Length > 4096 || input.Password.Any(char.IsControl))
        throw new ConsoleException(400, "请填写有效的MySQL管理账号、端口和密码");
    var result = await host.RunAsync(new("console", "mysql-configure", "", "", "", Guid.NewGuid().ToString(), MySql: input));
    if (!result.Ok) throw new ConsoleException(409, result.Message);
    await store.WriteAsync(s => { s.Audit.Add(new(clock.GetUtcNow(), "mysql.configured", null, context.Connection.RemoteIpAddress?.ToString() ?? "unknown", "MySQL connection verified; credentials not logged")); return true; });
    return result.MySql;
});
app.Map("/api/{**path}", () => Results.NotFound(new { message = "接口不存在" }));
app.MapFallbackToFile("index.html");
app.Run();

public partial class Program;
