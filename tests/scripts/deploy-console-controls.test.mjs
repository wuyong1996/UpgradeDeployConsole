import assert from 'node:assert/strict';
import { test } from 'node:test';
import { needsStartAll } from '../../src/frontend/src/project-controls.ts';

const project = { shutdownRequested: false };
const target = { front: {}, back: {}, databaseKind: 'mysql' };
const stopped = { ok: true, front: { state: 'stopped' }, back: { state: 'stopped' }, database: { state: 'paused' } };

test('total shutdown offers resume even before the next status refresh', () => {
  assert.equal(needsStartAll({ shutdownRequested: true }, target, undefined, true), true);
  assert.equal(needsStartAll({ shutdownRequested: true }, undefined, stopped, false), false);
});
test('all independently stopped services offer resume, running service keeps stop', () => {
  assert.equal(needsStartAll(project, target, stopped, false), true);
  for (const side of ['front', 'back', 'database']) {
    assert.equal(needsStartAll(project, target, { ...stopped, [side]: { state: 'running' } }, false), false);
  }
});
test('unknown or stale service observations do not imply shutdown', () => {
  assert.equal(needsStartAll(project, target, stopped, true), false);
  assert.equal(needsStartAll(project, target, { ...stopped, ok: false }, false), false);
  assert.equal(needsStartAll(project, target, { ...stopped, back: null }, false), false);
});
test('projects with only one service and no database can resume', () => {
  assert.equal(needsStartAll(project, { ...target, back: null, databaseKind: 'none' }, stopped, false), true);
  assert.equal(needsStartAll(project, { ...target, front: null, back: null, databaseKind: 'none' }, stopped, false), false);
});
