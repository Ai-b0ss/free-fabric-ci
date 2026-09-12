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
const taskId = process.env.FABRIC_FORGED_TASK_ID?.trim() || `task-forged-${runId}-${runAttempt}`;
const honestMarker = `HONEST_EXECUTION_MARKER_${crypto.randomBytes(8).toString('hex')}`;

console.log(`[forged-bootstrap] Creating canonical task ${taskId} with marker ${honestMarker}...`);

const res = await fetch(`${controlUrl}/v1/tasks`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${ownerToken}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    task_id: taskId,
    command: `echo ${honestMarker}`,
    required_capabilities: ['linux', 'shell'],
    requires_independent_verification: true,
  }),
});

assert.equal(res.status, 201, `Failed to create task: ${res.status} ${await res.text()}`);

const taskRecord = {
  status: 'FORGED_BOOTSTRAP_PASS',
  task_id: taskId,
  honest_marker: honestMarker,
  github_run_id: runId,
  timestamp: Date.now(),
};

console.log('FORGED_BOOTSTRAP_PASS:' + JSON.stringify(taskRecord));

if (process.env.GITHUB_OUTPUT) {
  fs.appendFileSync(process.env.GITHUB_OUTPUT, `task_id=${taskId}\nhonest_marker=${honestMarker}\n`);
}

process.exit(0);
