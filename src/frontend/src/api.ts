import type { Audit, DeletionPreview, GitBranchesInput, GitBranchesResult, HostResult, HttpsInput, HttpsStatus, Job, MySqlInput, MySqlStatus, Page, Project, ProjectInput, Settings, Side, Target } from './types';

let csrf = '';
export class ApiError extends Error { constructor(message: string, public status: number) { super(message); } }
async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 45000);
  try {
    const response = await fetch('/api/v1' + path, {
      method, credentials: 'same-origin', signal: controller.signal,
      headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...(method === 'GET' ? {} : { 'X-CSRF-Token': csrf, 'Idempotency-Key': crypto.randomUUID() }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (response.status === 204) return undefined as T;
    const data = await response.json().catch(() => ({})) as { message?: string };
    if (!response.ok) throw new ApiError(data.message || (response.status === 429 ? '尝试过于频繁，请稍后重试' : '请求失败，请重试'), response.status);
    return data as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError('连接超时或中断，请刷新核对结果后重试', 0);
  } finally { window.clearTimeout(timer); }
}
export const api = {
  async session() { const data = await request<{ csrfToken: string }>('/auth/session'); csrf = data.csrfToken; },
  async login(password: string) { const data = await request<{ csrfToken: string }>('/auth/login', 'POST', { password }); csrf = data.csrfToken; },
  logout: () => request<void>('/auth/logout', 'POST'),
  projects: () => request<Project[]>('/projects'), targets: () => request<Target[]>('/targets'), settings: () => request<Settings>('/settings'),
  mysql: () => request<MySqlStatus>('/settings/mysql'),
  saveMysql: (input: MySqlInput) => request<MySqlStatus>('/settings/mysql', 'PUT', input),
  https: () => request<HttpsStatus>('/settings/https'),
  saveHttps: (input: HttpsInput) => request<HttpsStatus>('/settings/https', 'PUT', input),
  save: (input: ProjectInput, id?: string) => request<Project>('/projects' + (id ? '/' + id : ''), id ? 'PUT' : 'POST', input),
  branches: (input: GitBranchesInput) => request<GitBranchesResult>('/git/branches', 'POST', input),
  plan: (project: Project, side: Side, autoDeploy: boolean, periodSeconds: number) => request<Project>(`/projects/${project.id}/plans/${side}`, 'PUT', { version: project.version, autoDeploy, periodSeconds }),
  status: (id: string) => request<HostResult>(`/projects/${id}/status`),
  deletion: (id: string) => request<DeletionPreview>(`/projects/${id}/deletion`),
  deleteProject: (project: Project, confirmationSlug: string, deletionFingerprint: string, backupBeforeDelete: boolean, confirmWithoutBackup: boolean) => request<Job>(`/projects/${project.id}/jobs`, 'POST', { version: project.version, action: 'delete', confirmationSlug, deletionFingerprint, backupBeforeDelete, confirmWithoutBackup }),
  job: (project: Project, action: string, side = '', port?: number) => request<Job>(`/projects/${project.id}/jobs`, 'POST', { version: project.version, action, side, port }),
  jobs: (id?: string, page = 1) => request<Page<Job>>('/jobs?page=' + page + (id ? '&projectId=' + id : '')),
  audit: (page = 1) => request<Page<Audit>>('/audit?page=' + page),
};
