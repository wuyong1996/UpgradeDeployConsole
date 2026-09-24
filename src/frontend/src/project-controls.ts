import type { HostResult, Project, Target } from './types';

export function needsStartAll(project: Project | undefined, target: Target | undefined, status: HostResult | undefined, stale: boolean): boolean {
  if (!project || !target || project.deletionRequested) return false;
  if (project.shutdownRequested) return true;
  if (stale || !status?.ok) return false;
  const sides = (['front', 'back'] as const).filter(side => target[side]);
  const databaseStopped = target.databaseKind === 'none' || ['stopped', 'paused'].includes(status.database?.state ?? '');
  return sides.length > 0 && sides.every(side => status[side]?.state === 'stopped') && databaseStopped;
}
