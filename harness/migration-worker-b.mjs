import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import fsSync from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import {
  assertRoleIsolation,
  sha256Json,
  mintProviderTicket,
  uploadEvidenceArtifact,
} from './common.mjs';

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

// 1. Filesystem isolation check
for (const p of ['step1.json', 'verify.mjs', 'worker-a-evidence.json']) {
  if (fsSync.existsSync(p)) {
    console.error(`FILESYSTEM_ISOLATION_VIOLATION: residual file ${p} detected`);
    process.exit(98);
  }
}

const workerBJobId = process.env.GITHUB_JOB || `job-b-${crypto.randomBytes(4).toString('hex')}`;
const workerBId = `worker-b-${process.env.GITHUB_RUN_ID || process.pid}-${workerBJobId}`;

// 2. Mint provider ticket for Gen 2
console.log(`[worker-b] Minting provider ticket for Gen 2...`);
const ticket = await mintProviderTicket(controlUrl, runnerToken, taskId, 2, 'circle-docker-primary');

// 3. Claim Generation 2
console.log(`[worker-b] Claiming Generation 2 as ${workerBId}...`);
const claimRes = await fetch(`${controlUrl}/v1/tasks/claim`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${runnerToken}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    worker_id: workerBId,
    capabilities: ['linux', 'shell'],
    task_id: taskId,
    lease_ms: 90000,
  }),
});
assert.equal(claimRes.status, 200, `Claim failed: ${claimRes.status}`);
const claimData = await claimRes.json();
assert.equal(claimData.task.generation, 2, 'Worker B must claim Generation 2');
const leaseId = claimData.lease.lease_id;

// 4. Fetch checkpoint from external task state
console.log(`[worker-b] Fetching checkpoint from Cloudflare...`);
const taskRes = await fetch(`${controlUrl}/v1/tasks/${encodeURIComponent(taskId)}`, {
  headers: { 'Authorization': `Bearer ${runnerToken}` },
});
assert.equal(taskRes.status, 200);
const { task } = await taskRes.json();
const cpArtifact = task.checkpoint?.artifact;
assert.ok(cpArtifact, 'Missing checkpoint artifact in canonical task');

// Assert distinct executions
if (cpArtifact.worker_a_job_id) {
  assert.notEqual(workerBJobId, cpArtifact.worker_a_job_id, 'Worker B job ID must differ from Worker A job ID');
}

// 5. Restore files into fresh temp directory
const freshDir = await fs.mkdtemp(path.join(os.tmpdir(), 'fabric-worker-b-restore-'));
for (const file of cpArtifact.files || []) {
  const targetPath = path.join(freshDir, file.path);
  await fs.mkdir(path.dirname(targetPath), { recursive: true });
  await fs.writeFile(targetPath, file.content, 'utf8');
}

// 6. Execute restored verify.mjs
console.log(`[worker-b] Executing restored verify.mjs in ${freshDir}...`);
const execResult = await new Promise((resolve) => {
  const child = spawn(process.execPath, [path.join(freshDir, 'verify.mjs')], {
    cwd: freshDir,
    env: { ...process.env, FABRIC_WORKER_ID: workerBId },
  });
  let stdout = '', stderr = '';
  child.stdout.on('data', c => stdout += c);
  child.stderr.on('data', c => stderr += c);
  child.on('close', code => resolve({ code: code ?? 1, stdout, stderr }));
});

assert.equal(execResult.code, 0, `Execution failed: ${execResult.stderr}`);
assert.ok(execResult.stdout.includes('RESTORED_GEN1_MARKER'), 'Execution output must contain RESTORED_GEN1_MARKER');
if (cpArtifact.marker) {
  assert.ok(execResult.stdout.includes(cpArtifact.marker), `Execution output must contain marker ${cpArtifact.marker}`);
}

// 7. Store raw execution evidence in Artifact Store
console.log(`[worker-b] Uploading raw execution evidence to Artifact Store...`);
const rawEvidence = Buffer.from(execResult.stdout, 'utf8');
const { artifactId: evidenceArtifactId, rawSha: evidenceSha } = await uploadEvidenceArtifact(
  controlUrl,
  ticket,
  taskId,
  2,
  'circle-docker-primary',
  workerBId,
  leaseId,
  2,
  rawEvidence
);

// 8. Submit ResultCandidate
console.log(`[worker-b] Submitting ResultCandidate...`);
const resultArtifact = {
  task_id: taskId,
  generation: 2,
  backend_id: 'circle-docker-primary',
  exit_code: 0,
  restored_checkpoint_hash: task.checkpoint.payload_sha256,
  execution_evidence_ref: evidenceArtifactId,
  execution_evidence_sha256: evidenceSha,
  external_identity: {
    job_id: workerBJobId,
    worker_id: workerBId,
  },
};
const resultSha = sha256Json(resultArtifact);

const resultRes = await fetch(`${controlUrl}/v1/tasks/result`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${runnerToken}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    task_id: taskId,
    generation: 2,
    fence: 2,
    lease_id: leaseId,
    payload_sha256: resultSha,
    artifact: resultArtifact,
  }),
});

assert.ok(resultRes.status === 200 || resultRes.status === 202, `Failed to submit result: ${resultRes.status}`);

console.log('WORKER_B_MIGRATION_PASS:' + JSON.stringify({
  status: 'WORKER_B_COMPLETED',
  task_id: taskId,
  worker_b_job_id: workerBJobId,
  evidence_ref: evidenceArtifactId,
  evidence_sha256: evidenceSha,
}));

process.exit(0);
