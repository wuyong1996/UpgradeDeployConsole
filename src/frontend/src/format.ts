import type { ProjectInput } from './types';
import Pinyin from 'tiny-pinyin';
export const periods = [{ value: 60, label: '每分钟' }, { value: 300, label: '每 5 分钟' }, { value: 900, label: '每 15 分钟' }, { value: 1800, label: '每 30 分钟' }, { value: 3600, label: '每小时' }, { value: 86400, label: '每天' }, { value: 0, label: '永不' }];
export const sideNames: Record<string, string> = { front: '前端', back: '后端', database: '数据库' };
export const actionNames: Record<string, string> = { delete: '删除项目', prepare: '拉取代码并准备发布', publish: '发布项目', check: '检查更新', deploy: '发布', rollback: '回退应用', start: '启动', stop: '停止', restart: '重启', port: '修改端口', 'stop-all': '总关停', 'start-all': '总开始' };
export const stateNames: Record<string, string> = { queued: '排队中', running: '运行中', succeeded: '成功', failed: '失败', cancelled: '已取消', interrupted: '已中断', stopped: '已停止', paused: '访问已暂停', pending: '待初始化', conflict: '需处理冲突', unknown: '未知' };
export function date(value: string | null | undefined) { return value ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(value)) : '—'; }
export function defaults(slug: string) { return { frontPath: `/www/wwwroot/${slug}/current`, backPath: `/opt/${slug}/current`, repositoryPath: `/opt/${slug}/repository` }; }
export function updateDefaultPaths(input: ProjectInput, oldSlug: string) { const old = defaults(oldSlug); const next = defaults(input.slug); for (const field of ['frontPath', 'backPath', 'repositoryPath'] as const) if (input[field] === old[field]) input[field] = next[field]; }
export function nextSlug(used: string[]) { let slug = 'project', number = 2; while (used.includes(slug)) slug = 'project-' + number++; return slug; }
export function projectSlug(name: string, used: string[]) {
  const normalized = name.normalize('NFKD').replace(/\p{M}/gu, '');
  const latin = Pinyin.isSupported()
    ? Pinyin.parse(normalized).map(token => token.type === 2 ? token.target.charAt(0).toLowerCase() : token.target).join('')
    : normalized;
  const readable = latin.replace(/([a-z0-9])([A-Z])/g, '$1-$2').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  const base = (readable ? (/^[a-z]/.test(readable) ? readable : `p-${readable}`) : 'project').slice(0, 24).replace(/-+$/g, '');
  const occupied = new Set(used);
  let slug = base, number = 2;
  while (occupied.has(slug)) {
    const suffix = `-${number++}`;
    slug = base.slice(0, 24 - suffix.length).replace(/-+$/g, '') + suffix;
  }
  return slug;
}
