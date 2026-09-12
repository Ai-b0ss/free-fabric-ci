import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import { assertRoleIsolation } from './common.mjs';

assertRoleIsolation('owner');

const controlUrl = process.env.FABRIC_CONTROL_URL?.trim();
const ownerToken = process.env.FABRIC_OWNER_TOKEN?.trim();

if (!controlUrl || !ownerToken) {
  console.error('FATAL: FABRIC_CONTROL_URL and FABRIC_OWNER_TOKEN are required');
  process.exit(2);
}

const runId = process.env.GITHUB_RUN_ID || Date.now();
const runAttempt = process.env.GITHUB_RUN_ATTEMPT || 1;
const taskId = process.env.FABRIC_TASK_ID?.trim() || `task-live-mig-${runId}-${runAttempt}`;
const expectedMarker = `GENUINE_MIGRATION_MARKER_${crypto.randomBytes(8).toString('hex')}`;

console.log(`[migration-bootstrap] Creating canonical task ${taskId} with marker ${expectedMarker}...`);

const res = await fetch(`${controlUrl}/v1/tasks`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${ownerToken}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    task_id: taskId,
    command: 'node verify.mjs',
    required_capabilities: ['linux', 'shell'],
    requires_independent_verification: true,
  }),
});

assert.equal(res.status, 201, `Failed to create task: ${res.status} ${await res.text()}`);

const taskRecord = {
  status: 'BOOTSTRAP_PASS',
  task_id: taskId,
  expected_marker: expectedMarker,
  github_run_id: runId,
  github_run_attempt: runAttempt,
  timestamp: Date.now(),
};

console.log('BOOTSTRAP_PASS:' + JSON.stringify(taskRecord));

if (process.env.GITHUB_OUTPUT) {
  fs.appendFileSync(process.env.GITHUB_OUTPUT, `task_id=${taskId}\nexpected_marker=${expectedMarker}\n`);
}

process.exit(0);
