import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { assertRoleIsolation, sha256Json } from './common.mjs';

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

const randomMarker = process.env.FABRIC_EXPECTED_MARKER?.trim() || `MARKER_${crypto.randomBytes(8).toString('hex')}`;
const workerAJobId = process.env.GITHUB_JOB || `job-a-${crypto.randomBytes(4).toString('hex')}`;
const workerAId = `worker-a-${process.env.GITHUB_RUN_ID || process.pid}-${workerAJobId}`;

console.log(`[worker-a] Target task: ${taskId}, Worker ID: ${workerAId}, Job: ${workerAJobId}`);

// 1. Claim Generation 1
let claimG1;
for (let attempt = 1; attempt <= 5; attempt++) {
  const res = await fetch(`${controlUrl}/v1/tasks/claim`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${runnerToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      worker_id: workerAId,
      capabilities: ['linux', 'shell'],
      lease_ms: 90000,
      task_id: taskId,
    }),
  });
  if (res.ok) {
    claimG1 = await res.json();
    break;
  }
  if (attempt < 5) await new Promise(r => setTimeout(r, 1000));
}

assert.ok(claimG1 && claimG1.task, 'Worker A failed to claim task');
assert.equal(claimG1.task.generation, 1, 'Worker A must claim Generation 1');
assert.equal(claimG1.task.state, 'RUNNING', 'Task state must be RUNNING');
const leaseId = claimG1.lease.lease_id;
console.log(`[worker-a] Claimed Generation 1 with lease ${leaseId}`);

// 2. Perform Phase 1 work on disk
const workDir = await fs.mkdtemp(path.join(os.tmpdir(), 'fabric-worker-a-'));
const step1Data = {
  phase: 1,
  marker: randomMarker,
  worker_id: workerAId,
  job_id: workerAJobId,
  timestamp: Date.now(),
};
await fs.writeFile(path.join(workDir, 'step1.json'), JSON.stringify(step1Data, null, 2), 'utf8');

const verifyScript = `import fs from "node:fs";
const data = JSON.parse(fs.readFileSync("step1.json", "utf8"));
console.log("RESTORED_GEN1_MARKER: " + data.marker);
console.log("CONTINUATION_EXECUTED_BY: " + (process.env.FABRIC_WORKER_ID || "unknown"));
`;
await fs.writeFile(path.join(workDir, 'verify.mjs'), verifyScript, 'utf8');

// 3. Create durable checkpoint payload
const checkpointPayload = {
  step: 1,
  marker: randomMarker,
  files: [
    { path: 'step1.json', content: JSON.stringify(step1Data) },
    { path: 'verify.mjs', content: verifyScript },
  ],
  lease_id: leaseId,
  fence: claimG1.lease.fence ?? 1,
  generation: 1,
  worker_id: workerAId,
  worker_a_job_id: workerAJobId,
  github_run_id: process.env.GITHUB_RUN_ID || null,
};
const cpHash = sha256Json(checkpointPayload);

// 4. Save checkpoint to Cloudflare
console.log(`[worker-a] Saving checkpoint with SHA ${cpHash}...`);
const cpRes = await fetch(`${controlUrl}/v1/tasks/checkpoint`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${runnerToken}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    task_id: taskId,
    generation: 1,
    fence: 1,
    lease_id: leaseId,
    payload_sha256: cpHash,
    artifact: checkpointPayload,
  }),
});
assert.equal(cpRes.status, 200, `Failed to save checkpoint: ${cpRes.status} ${await cpRes.text()}`);

console.log('WORKER_A_MIGRATION_PASS:' + JSON.stringify({
  status: 'WORKER_A_COMPLETED',
  task_id: taskId,
  lease_id: leaseId,
  checkpoint_hash: cpHash,
  marker: randomMarker,
}));

process.exit(0);
