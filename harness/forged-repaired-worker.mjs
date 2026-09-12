import assert from 'node:assert/strict';
import crypto from 'node:crypto';
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

const taskId = process.env.FABRIC_FORGED_TASK_ID?.trim();
const honestMarker = process.env.FABRIC_HONEST_MARKER?.trim();

if (!taskId || !honestMarker) {
  console.error('FATAL: FABRIC_FORGED_TASK_ID and FABRIC_HONEST_MARKER are required');
  process.exit(2);
}

// 1. Mint ticket for Gen 2
console.log(`[repaired-worker] Minting provider ticket for Gen 2...`);
const ticket = await mintProviderTicket(controlUrl, runnerToken, taskId, 2, 'circle-docker-primary');

// 2. Claim Gen 2
const repairedWorkerId = `repaired-worker-${crypto.randomBytes(4).toString('hex')}`;
console.log(`[repaired-worker] Claiming Generation 2 as ${repairedWorkerId}...`);
const claimRes = await fetch(`${controlUrl}/v1/tasks/claim`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${runnerToken}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    worker_id: repairedWorkerId,
    capabilities: ['linux', 'shell'],
    task_id: taskId,
    lease_ms: 60000,
  }),
});
assert.equal(claimRes.status, 200);
const claimData = await claimRes.json();
assert.equal(claimData.task.generation, 2);
const leaseId = claimData.lease.lease_id;

// 3. Perform genuine work producing honestMarker
console.log(`[repaired-worker] Executing genuine command producing honestMarker...`);
const goodRawEvidence = Buffer.from(`${honestMarker}\nGENUINE_REPAIRED_EXECUTION_PASS\n`, 'utf8');
const { artifactId: goodArtifactId, rawSha: goodRawSha } = await uploadEvidenceArtifact(
  controlUrl,
  ticket,
  taskId,
  2,
  'circle-docker-primary',
  repairedWorkerId,
  leaseId,
  2,
  goodRawEvidence
);

// 4. Submit genuine candidate
console.log(`[repaired-worker] Submitting repaired candidate...`);
const repairedArtifact = {
  task_id: taskId,
  generation: 2,
  backend_id: 'circle-docker-primary',
  exit_code: 0,
  execution_evidence_ref: goodArtifactId,
  execution_evidence_sha256: goodRawSha,
  honest_marker: honestMarker,
};
const repairedSha = sha256Json(repairedArtifact);

const submitRes = await fetch(`${controlUrl}/v1/tasks/result`, {
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
    payload_sha256: repairedSha,
    artifact: repairedArtifact,
  }),
});

assert.ok(submitRes.status === 200 || submitRes.status === 202, `Submit failed: ${submitRes.status}`);

console.log('REPAIRED_SUBMISSION_PASS:' + JSON.stringify({
  status: 'REPAIRED_CANDIDATE_SUBMITTED',
  task_id: taskId,
  good_evidence_ref: goodArtifactId,
  good_evidence_sha256: goodRawSha,
}));

process.exit(0);
