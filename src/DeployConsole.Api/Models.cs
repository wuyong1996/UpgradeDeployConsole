namespace DeployConsole;

public sealed class ConsoleOptions
{
    public string DataDirectory { get; set; } = "/var/lib/deploy-console";
    public string PasswordFile { get; set; } = "/etc/deploy-console/password.txt";
    public string TargetsDirectory { get; set; } = "/etc/deploy-console/targets";
    public string HelperPath { get; set; } = "/usr/local/lib/deploy-console/host.py";
    public bool AllowLoopbackHttp { get; set; }
    public string[] AllowedGitHosts { get; set; } = ["github.com", "gitlab.com", "gitee.com"];
}

public sealed class DeploymentProject
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public long Version { get; set; } = 1;
    public string Slug { get; set; } = "project";
    public string Name { get; set; } = "";
    public string Environment { get; set; } = "test";
    public string Repository { get; set; } = "";
    public string Branch { get; set; } = "main";
    public string GitAuthMode { get; set; } = "none";
    public string GitUsername { get; set; } = "";
    public string FrontPath { get; set; } = "";
    public string BackPath { get; set; } = "";
    public string RepositoryPath { get; set; } = "";
    public bool ShutdownRequested { get; set; }
    public bool DeletionRequested { get; set; }
    public ServicePlan Front { get; set; } = new();
    public ServicePlan Back { get; set; } = new();
    public ServicePlan Plan(string side) => side == "front" ? Front : side == "back" ? Back : throw new ConsoleException(400, "服务类型无效");
}

public sealed class ServicePlan
{
    public bool AutoDeploy { get; set; }
    public int PeriodSeconds { get; set; } = 60;
    public bool Stopped { get; set; }
    public DateTimeOffset? NextRunAt { get; set; }
    public DateTimeOffset? LastCheckAt { get; set; }
    public string? LastCommit { get; set; }
    public string? LastResult { get; set; }
}

public sealed class OperationJob
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid ProjectId { get; set; }
    public string Action { get; set; } = "";
    public string Side { get; set; } = "";
    public int? Port { get; set; }
    public string? Commit { get; set; }
    public string Trigger { get; set; } = "manual";
    public string State { get; set; } = "queued";
    public string Step { get; set; } = "排队";
    public string IdempotencyKey { get; set; } = "";
    public string RequestHash { get; set; } = "";
    public long ProjectVersion { get; set; }
    public string Repository { get; set; } = "";
    public string Branch { get; set; } = "";
    public DateTimeOffset CreatedAt { get; set; }
    public DateTimeOffset? FinishedAt { get; set; }
    public List<JobEvent> Events { get; set; } = [];
    public HostResult? Result { get; set; }
    public bool EnableSchedule { get; set; }
    public string? DeletionFingerprint { get; set; }
    public bool BackupBeforeDelete { get; set; } = true;
    public string? ProjectName { get; set; }
}
public sealed record JobEvent(DateTimeOffset At, string Message);
public sealed class ConsoleState
{
    public Dictionary<Guid, string> GitSecrets { get; set; } = [];
    public List<DeploymentProject> Projects { get; set; } = [];
    public List<OperationJob> Jobs { get; set; } = [];
    public List<AuditEntry> Audit { get; set; } = [];
    public List<WriteReceipt> Writes { get; set; } = [];
}
public sealed record AuditEntry(DateTimeOffset At, string Action, Guid? ProjectId, string Source, string Detail);
public sealed record WriteReceipt(string Key, string Hash, Guid ProjectId);
public sealed record LoginRequest(string Password);
public sealed record ProjectInput(long Version, string Slug, string Name, string Environment, string Repository, string Branch, string FrontPath, string BackPath, string RepositoryPath, GitAuthInput? GitAuth = null, string? BranchVerification = null, bool DeployAfterSave = false, bool EnableSchedule = false);
public sealed record GitAuthInput(string Mode = "none", string Username = "", string? Secret = null, bool UseStored = false);
public sealed record GitBranchesInput(string Repository, string Slug, Guid? ProjectId = null, GitAuthInput? GitAuth = null);
public sealed record GitCredential(string Repository, string Username, string Secret)
{
    public override string ToString() => "GitCredential [redacted]";
}
public sealed record GitBranchesResult(List<RemoteBranch> Branches, string? DefaultBranch, string Verification);
public sealed record PlanInput(long Version, bool AutoDeploy, int PeriodSeconds);
public sealed record JobInput(long Version, string Action, string Side = "", int? Port = null, string? Commit = null, string? ConfirmationSlug = null, string? DeletionFingerprint = null, bool BackupBeforeDelete = true, bool ConfirmWithoutBackup = false);
public sealed record TargetService(string Kind, string? Unit, int Port, string Current, bool IndependentDeploy = false);
public sealed record TargetSummary(string Slug, TargetService? Front, TargetService? Back, string DatabaseKind, string? DatabaseIdentity, bool DatabaseManaged, string RepositoryPath, string[] AllowedGitHosts);
public sealed record ServiceStatus(string State, string Health, int? Port, string? Path, bool? BootEnabled = null, string? Commit = null, string? AccessUrl = null, string? DatabaseName = null, string? Host = null, string? Username = null, string? MigrationState = null, int? AppliedMigrations = null);
public sealed record DeletionResource(string Kind, string Name);
public sealed record DeletionPreview(string Fingerprint, List<DeletionResource> Resources, string? BackupDirectory, bool Resume = false, bool BackupBeforeDelete = true, bool BackupModeLocked = false);
public sealed record RemoteBranch(string Name, string Commit);
public sealed class HostResult
{
    public bool Ok { get; set; }
    public bool NoChanges { get; set; }
    public string Message { get; set; } = "";
    public string? Commit { get; set; }
    public ServiceStatus? Front { get; set; }
    public ServiceStatus? Back { get; set; }
    public ServiceStatus? Database { get; set; }
    public List<RemoteBranch> Branches { get; set; } = [];
    public string? DefaultBranch { get; set; }
    public MySqlStatus? MySql { get; set; }
    public HttpsStatus? Https { get; set; }
    public DeletionPreview? Deletion { get; set; }
    public bool? BackupBeforeDelete { get; set; }
    public string? BackupDirectory { get; set; }
}
public sealed record HttpsInput(string Domain, string CertificatePath, string PrivateKeyPath);
public sealed record HttpsStatus(bool Configured, string Domain, string CertificatePath, string PrivateKeyPath, DateTimeOffset? ExpiresAt = null, DateTimeOffset? AppliedAt = null, DateTimeOffset? LastCheckedAt = null, string? SyncError = null);
public sealed record MySqlStatus(bool Configured, string Username, int Port);
public sealed record MySqlInput(string Username, int Port, string Password)
{
    public override string ToString() => "MySqlInput [redacted]";
}
public sealed record SetupPaths(string FrontPath, string BackPath, string RepositoryPath);
public sealed record HostRequest(string Slug, string Action, string Side, string Repository, string Branch, string OperationId, int? Port = null, string? Commit = null, GitCredential? Credential = null, SetupPaths? Setup = null, MySqlInput? MySql = null, long? RequestedAtUnixMs = null, HttpsInput? Https = null, string? DeletionFingerprint = null, bool BackupBeforeDelete = true, bool ConfirmWithoutBackup = false);
public sealed class ConsoleException(int status, string message) : Exception(message)
{
    public int Status { get; } = status;
}
