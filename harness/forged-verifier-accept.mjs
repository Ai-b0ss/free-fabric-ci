import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import {
  assertRoleIsolation,
  sha256Json,
  createAttestation,
} from './common.mjs';

assertRoleIsolation('verifier');

const controlUrl = process.env.FABRIC_CONTROL_URL?.trim();
const verifierToken = process.env.FABRIC_VERIFIER_TOKEN?.trim();

if (!controlUrl || !verifierToken) {
  console.error('FATAL: FABRIC_CONTROL_URL and FABRIC_VERIFIER_TOKEN are required');
  process.exit(2);
}

const taskId = process.env.FABRIC_FORGED_TASK_ID?.trim();
const honestMarker = process.env.FABRIC_HONEST_MARKER?.trim();

if (!taskId || !honestMarker) {
  console.error('FATAL: FABRIC_FORGED_TASK_ID and FABRIC_HONEST_MARKER are required');
  process.exit(2);
}

// 1. Fetch canonical task and candidate
console.log(`[forged-verifier-accept] Fetching canonical task ${taskId}...`);
const taskRes = await fetch(`${controlUrl}/v1/tasks/${encodeURIComponent(taskId)}`, {
  headers: { 'Authorization': `Bearer ${verifierToken}` },
});
assert.equal(taskRes.status, 200);
const { task } = await taskRes.json();
assert.equal(task.generation, 2);

const candidate = task.result_candidate;
assert.ok(candidate, 'Missing candidate');
const artifact = candidate.artifact;
const evidenceRef = artifact.execution_evidence_ref;
const evidenceExpectedSha = artifact.execution_evidence_sha256;

// 2. Download and verify genuine evidence
console.log(`[forged-verifier-accept] Downloading evidence ${evidenceRef}...`);
const chunkRes = await fetch(`${controlUrl}/v1/artifacts/${evidenceRef}/chunks/0`, {
  headers: { 'Authorization': `Bearer ${verifierToken}` },
});
assert.equal(chunkRes.status, 200);
const rawBytes = Buffer.from(await chunkRes.arrayBuffer());
const downloadedSha = crypto.createHash('sha256').update(rawBytes).digest('hex');
assert.equal(downloadedSha.toLowerCase(), evidenceExpectedSha.toLowerCase());

const rawStdout = rawBytes.toString('utf8');
assert.ok(rawStdout.includes(honestMarker), 'Good evidence must contain honest marker');
console.log(`[forged-verifier-accept] Honest marker confirmed in raw evidence.`);

// 3. Create signed ResultAcceptance
const verifierId = `verifier-forged-${process.env.GITHUB_RUN_ID || process.pid}`;
const assertions = [
  { type: 'exit_code_zero', exit_code: artifact.exit_code },
  { type: 'honest_marker_verified', marker: honestMarker },
  { type: 'raw_evidence_sha256_match', sha256: downloadedSha },
  { type: 'target_backend_bound', target_backend_id: 'circle-docker-primary' },
];

const acceptance = await createAttestation({
  taskId,
  generation: 2,
  targetBackendId: 'circle-docker-primary',
  payloadSha256: candidate.payload_sha256,
  assertions,
  verifierSecret: verifierToken,
  verifierId,
});

// 4. Submit acceptance
console.log(`[forged-verifier-accept] Submitting acceptance...`);
const acceptRes = await fetch(`${controlUrl}/v1/tasks/accept`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${verifierToken}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    task_id: taskId,
    generation: 2,
    fence: 2,
    payload_sha256: candidate.payload_sha256,
    target_backend_id: 'circle-docker-primary',
    acceptance,
  }),
});
assert.equal(acceptRes.status, 200, `Accept failed: ${acceptRes.status} ${await acceptRes.text()}`);

const finalTaskRes = await fetch(`${controlUrl}/v1/tasks/${encodeURIComponent(taskId)}`, {
  headers: { 'Authorization': `Bearer ${verifierToken}` },
});
const finalTask = (await finalTaskRes.json()).task;
assert.equal(finalTask.state, 'DONE');

const consolidatedProof = {
  status: 'FORGED_ACCEPT_PASS',
  task_id: taskId,
  generation: 2,
  good_evidence_ref: evidenceRef,
  good_evidence_sha256: downloadedSha,
  result_acceptance_proof_hash: acceptance.proof_sha256,
  final_task_state: finalTask.state,
  timestamp: Date.now(),
};

console.log('FORGED_ACCEPT_PASS:' + JSON.stringify(consolidatedProof));
console.log('[forged-verifier-accept] Forged worker flow successfully accepted and task is DONE.');

process.exit(0);
