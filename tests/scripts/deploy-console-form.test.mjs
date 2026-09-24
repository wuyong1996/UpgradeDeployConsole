import assert from 'node:assert/strict';
import { test } from 'node:test';
import { defaults, projectSlug, updateDefaultPaths } from '../../src/frontend/src/format.ts';

test('Chinese project names produce short pinyin initials', () => {
  assert.equal(projectSlug('培训报名系统', []), 'pxbmxt');
  assert.equal(projectSlug('业务管理平台', []), 'ywglpt');
});

test('English, mixed names and accents produce valid readable identifiers', () => {
  assert.equal(projectSlug('TrainingRegistration', []), 'training-registration');
  assert.equal(projectSlug('Café Portal', []), 'cafe-portal');
  assert.equal(projectSlug('2026 培训', []), 'p-2026-px');
  assert.equal(projectSlug('CRM 客户管理', []), 'crm-khgl');
});

test('existing projects and reserved targets are skipped with a stable suffix', () => {
  assert.equal(projectSlug('培训报名系统', ['pxbmxt', 'pxbmxt-2']), 'pxbmxt-3');
  assert.equal(projectSlug('培训报名系统', ['other']), 'pxbmxt');
});

test('long names and duplicate suffixes stay within 24 characters', () => {
  const name = 'Very Long Project Name With Extra Words';
  const first = projectSlug(name, []);
  const second = projectSlug(name, [first]);
  assert.notEqual(first, second);
  assert.match(second, /-2$/);
  for (const slug of [first, second]) assert.match(slug, /^[a-z][a-z0-9-]{0,22}[a-z0-9]$/);
});

test('empty or unsupported names fall back to a unique generic identifier', () => {
  assert.equal(projectSlug('   ', []), 'project');
  assert.equal(projectSlug('🚀 !!!', ['project']), 'project-2');
});

test('generated identifiers update recommended paths and preserve manually entered paths', () => {
  const input = { ...defaults('project'), slug: projectSlug('培训报名系统', []), frontPath: '/srv/custom-frontend' };
  updateDefaultPaths(input, 'project');
  assert.equal(input.frontPath, '/srv/custom-frontend');
  assert.equal(input.backPath, '/opt/pxbmxt/current');
  assert.equal(input.repositoryPath, '/opt/pxbmxt/repository');
});
