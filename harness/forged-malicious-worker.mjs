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

// 1. Mint ticket for Gen 1
console.log(`[malicious-worker] Minting provider ticket for Gen 1...`);
const ticket = await mintProviderTicket(controlUrl, runnerToken, taskId, 1, 'circle-docker-primary');

// 2. Claim Gen 1
const maliciousWorkerId = `malicious-worker-${crypto.randomBytes(4).toString('hex')}`;
console.log(`[malicious-worker] Claiming Generation 1 as ${maliciousWorkerId}...`);
const claimRes = await fetch(`${controlUrl}/v1/tasks/claim`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${runnerToken}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    worker_id: maliciousWorkerId,
    capabilities: ['linux', 'shell'],
    task_id: taskId,
    lease_ms: 60000,
  }),
});
assert.equal(claimRes.status, 200);
const claimData = await claimRes.json();
const leaseId = claimData.lease.lease_id;

// 3. STRONG ATTACK: Execute contradictory real output and store contradictory evidence
console.log(`[malicious-worker] Executing wrong command and storing contradictory evidence...`);
const badRawEvidence = Buffer.from('WRONG_REAL_EXECUTION_OUTPUT_UNAUTHORIZED\n', 'utf8');
const { artifactId: badArtifactId, rawSha: badRawSha } = await uploadEvidenceArtifact(
  controlUrl,
  ticket,
  taskId,
  1,
  'circle-docker-primary',
  maliciousWorkerId,
  leaseId,
  1,
  badRawEvidence
);

// 4. Submit forged candidate metadata claiming success with honestMarker
console.log(`[malicious-worker] Submitting forged candidate claiming honestMarker...`);
const forgedArtifact = {
  task_id: taskId,
  generation: 1,
  backend_id: 'circle-docker-primary',
  exit_code: 0,
  execution_evidence_ref: badArtifactId,
  execution_evidence_sha256: badRawSha,
  claimed_marker: honestMarker,
};
const forgedSha = sha256Json(forgedArtifact);

const submitRes = await fetch(`${controlUrl}/v1/tasks/result`, {
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
    payload_sha256: forgedSha,
    artifact: forgedArtifact,
  }),
});

assert.ok(submitRes.status === 200 || submitRes.status === 202, `Submit failed: ${submitRes.status}`);

console.log('FORGED_SUBMISSION_PASS:' + JSON.stringify({
  status: 'FORGED_CANDIDATE_SUBMITTED',
  task_id: taskId,
  bad_evidence_ref: badArtifactId,
  bad_evidence_sha256: badRawSha,
}));

process.exit(0);
