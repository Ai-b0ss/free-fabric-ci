import assert from 'node:assert/strict';
import { assertRoleIsolation } from './common.mjs';

assertRoleIsolation('worker');

const controlUrl = process.env.FABRIC_CONTROL_URL?.trim();
const runnerToken = process.env.FABRIC_RUNNER_TOKEN?.trim();

if (!controlUrl || !runnerToken) {
  console.error('FATAL: FABRIC_CONTROL_URL and FABRIC_RUNNER_TOKEN are required');
  process.exit(2);
}

const taskId = process.env.FABRIC_TASK_ID?.trim();
if (!taskId) {
  console.error('FATAL: FABRIC_TASK_ID is required');
  process.exit(2);
}

console.log(`[migration-stale-a] Recovering Gen-1 lease for task ${taskId}...`);

const taskRes = await fetch(`${controlUrl}/v1/tasks/${encodeURIComponent(taskId)}`, {
  headers: { 'Authorization': `Bearer ${runnerToken}` },
});
assert.equal(taskRes.status, 200, `Task fetch failed: ${taskRes.status}`);
const { task } = await taskRes.json();
assert.equal(task.generation, 2, 'Task must be at Generation 2');
assert.equal(task.state, 'READY', 'Task must be READY');

const staleCheckpoint = task.checkpoint?.artifact;
assert.ok(staleCheckpoint, 'Missing checkpoint in canonical task');
const staleLeaseId = staleCheckpoint.lease_id;
assert.ok(staleLeaseId, 'Missing Gen-1 lease ID');

console.log(`[migration-stale-a] Attempting mutation with Gen-1 lease ${staleLeaseId}...`);

const staleRes = await fetch(`${controlUrl}/v1/tasks/result`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${runnerToken}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    task_id: taskId,
    generation: 1,
    fence: 1,
    lease_id: staleLeaseId,
    payload_sha256: '0'.repeat(64),
    artifact: { stale: true, attacker: 'zombie-worker-a' },
  }),
});

assert.equal(staleRes.status, 409, `Stale mutation must be rejected with 409, got ${staleRes.status}`);
const staleBody = await staleRes.json();
assert.equal(staleBody.status, 'STALE_FENCE', `Expected STALE_FENCE error code, got ${staleBody.status}`);
assert.ok(staleBody.receipt, 'Expected server-authored rejection receipt in body');
assert.equal(staleBody.receipt.attempted_lease_id, staleLeaseId);
assert.equal(Number(staleBody.receipt.attempted_generation), 1);
assert.equal(Number(staleBody.receipt.current_generation), 2);
assert.ok(staleBody.receipt.mutation_sha256, 'Server receipt must include mutation_sha256');

console.log('STALE_EVIDENCE_PASS:' + JSON.stringify({
  status: 'STALE_FENCE_REJECTED',
  task_id: taskId,
  stale_lease_id: staleLeaseId,
  server_receipt_id: staleBody.receipt.receipt_id,
  mutation_sha256: staleBody.receipt.mutation_sha256,
  http_status: 409,
}));

process.exit(0);
