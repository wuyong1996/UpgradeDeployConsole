export type Side = 'front' | 'back';
export interface Plan { autoDeploy: boolean; periodSeconds: number; stopped: boolean; nextRunAt: string | null; lastCheckAt: string | null; lastCommit: string | null; lastResult: string | null }
export interface GitAuthInput { mode: 'none' | 'https'; username: string; secret?: string; useStored: boolean }
export interface RemoteBranch { name: string; commit: string }
export interface GitBranchesInput { repository: string; slug: string; projectId?: string; gitAuth: GitAuthInput }
export interface GitBranchesResult { branches: RemoteBranch[]; defaultBranch: string | null; verification: string }
export interface ProjectInput { version: number; slug: string; name: string; environment: string; repository: string; branch: string; frontPath: string; backPath: string; repositoryPath: string; gitAuth?: GitAuthInput; branchVerification?: string; deployAfterSave?: boolean; enableSchedule?: boolean }
export interface Project extends ProjectInput { id: string; front: Plan; back: Plan; shutdownRequested: boolean; deletionRequested: boolean; gitAuthMode: 'none' | 'https'; gitUsername: string }
export interface ServiceStatus { state: string; health: string; port: number | null; path: string | null; bootEnabled: boolean | null; commit: string | null; accessUrl?: string | null; databaseName?: string | null; host?: string | null; username?: string | null; migrationState?: string | null; appliedMigrations?: number | null }
export interface DeletionPreview { fingerprint: string; resources: { kind: string; name: string }[]; backupDirectory: string | null; resume: boolean; backupBeforeDelete: boolean; backupModeLocked: boolean }
export interface HostResult { backupBeforeDelete?: boolean | null; ok: boolean; message: string; commit: string | null; front: ServiceStatus | null; back: ServiceStatus | null; database: ServiceStatus | null; branches: { name: string; commit: string }[]; backupDirectory?: string | null }
export interface TargetService { kind: string; unit: string | null; port: number; current: string; independentDeploy: boolean }
export interface Target { slug: string; front: TargetService | null; back: TargetService | null; databaseKind: string; databaseIdentity: string | null; databaseManaged: boolean; repositoryPath: string; allowedGitHosts: string[] }
export interface Job { backupBeforeDelete: boolean; id: string; projectId: string; projectName?: string; action: string; side: string; trigger: string; state: string; step: string; createdAt: string; finishedAt: string | null; events: { at: string; message: string }[]; result: HostResult | null }
export interface Audit { at: string; action: string; projectId: string | null; source: string; detail: string }
export interface Page<T> { total: number; items: T[] }
export interface Settings { passwordFile: string; timezone: string; linux: boolean; periods: number[]; localMode: boolean }
export interface MySqlStatus { configured: boolean; username: string; port: number }
export interface MySqlInput { username: string; port: number; password: string }
export interface HttpsInput { domain: string; certificatePath: string; privateKeyPath: string }
export interface HttpsStatus extends HttpsInput { configured: boolean; expiresAt: string | null; appliedAt: string | null; lastCheckedAt: string | null; syncError: string | null }
