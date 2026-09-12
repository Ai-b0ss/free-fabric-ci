import assert from 'node:assert/strict';
import { assertRoleIsolation } from './common.mjs';

assertRoleIsolation('owner');

const controlUrl = process.env.FABRIC_CONTROL_URL?.trim();
const ownerToken = process.env.FABRIC_OWNER_TOKEN?.trim();

if (!controlUrl || !ownerToken) {
  console.error('FATAL: FABRIC_CONTROL_URL and FABRIC_OWNER_TOKEN are required');
  process.exit(2);
}

const taskId = process.env.FABRIC_TASK_ID?.trim();
if (!taskId) {
  console.error('FATAL: FABRIC_TASK_ID is required');
  process.exit(2);
}

console.log(`[migration-failover] Requeuing task ${taskId} to Generation 2...`);

const bumpRes = await fetch(`${controlUrl}/v1/tasks/requeue`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${ownerToken}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({ task_id: taskId }),
});

assert.equal(bumpRes.status, 200, `Failover failed: ${bumpRes.status} ${await bumpRes.text()}`);
const bumpData = await bumpRes.json();
assert.equal(bumpData.task.generation, 2, 'Generation must advance to 2');
assert.equal(bumpData.task.state, 'READY', 'State must transition to READY');

console.log('FAILOVER_MIGRATION_PASS:' + JSON.stringify({
  status: 'FAILOVER_PASS',
  task_id: taskId,
  generation: 2,
  state: 'READY',
}));

process.exit(0);
