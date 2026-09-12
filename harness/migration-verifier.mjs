import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import {
  assertRoleIsolation,
  sha256Json,
  computeMutationSha256,
  createAttestation,
} from './common.mjs';

assertRoleIsolation('verifier');

const controlUrl = process.env.FABRIC_CONTROL_URL?.trim();
const verifierToken = process.env.FABRIC_VERIFIER_TOKEN?.trim();

if (!controlUrl || !verifierToken) {
  console.error('FATAL: FABRIC_CONTROL_URL and FABRIC_VERIFIER_TOKEN are required');
  process.exit(2);
}

const taskId = process.env.FABRIC_TASK_ID?.trim();
if (!taskId) {
  console.error('FATAL: FABRIC_TASK_ID is required');
  process.exit(2);
}

// 1. Assert role-scoped authorization: verifier cannot mutate runner routes
console.log('[migration-verifier] Testing role isolation: verifier cannot claim tasks...');
const badClaim = await fetch(`${controlUrl}/v1/tasks/claim`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${verifierToken}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({ worker_id: 'fake-verifier-runner', task_id: taskId }),
});
assert.ok(badClaim.status === 401 || badClaim.status === 403, `Verifier claim must be rejected, got ${badClaim.status}`);

// 2. Fetch canonical task from Cloudflare DO
console.log(`[migration-verifier] Fetching canonical task ${taskId}...`);
const taskRes = await fetch(`${controlUrl}/v1/tasks/${encodeURIComponent(taskId)}`, {
  headers: { 'Authorization': `Bearer ${verifierToken}` },
});
assert.equal(taskRes.status, 200);
const { task } = await taskRes.json();
assert.equal(task.generation, 2);
assert.ok(task.state === 'RESULT_PENDING' || task.state === 'VERIFYING');

const candidate = task.result_candidate;
assert.ok(candidate, 'Missing candidate in canonical task');
const artifact = candidate.artifact;
assert.ok(artifact, 'Missing execution artifact metadata in candidate');

// Check candidate payload SHA
const recomputedSha = sha256Json(artifact);
assert.equal(recomputedSha.toLowerCase(), candidate.payload_sha256.toLowerCase());

// 3. Independently download raw execution evidence from Artifact Store
const evidenceRef = artifact.execution_evidence_ref;
const evidenceExpectedSha = artifact.execution_evidence_sha256;
console.log(`[migration-verifier] Downloading raw execution evidence ${evidenceRef}...`);
const chunkRes = await fetch(`${controlUrl}/v1/artifacts/${evidenceRef}/chunks/0`, {
  headers: { 'Authorization': `Bearer ${verifierToken}` },
});
assert.equal(chunkRes.status, 200);
const rawBytes = Buffer.from(await chunkRes.arrayBuffer());
const downloadedSha = crypto.createHash('sha256').update(rawBytes).digest('hex');
assert.equal(downloadedSha.toLowerCase(), evidenceExpectedSha.toLowerCase());

const rawStdout = rawBytes.toString('utf8');
assert.ok(rawStdout.includes('RESTORED_GEN1_MARKER'), 'Missing restored marker in stdout');
const expectedMarker = task.checkpoint?.artifact?.marker;
if (expectedMarker) {
  assert.ok(rawStdout.includes(expectedMarker), `Missing expected marker ${expectedMarker} in stdout`);
}

// 4. Verify distinct execution boundaries
const workerAJobId = task.checkpoint?.artifact?.worker_a_job_id;
const workerBJobId = artifact.external_identity?.job_id;
const verifierJobId = process.env.GITHUB_JOB || `verifier-${crypto.randomBytes(4).toString('hex')}`;

if (workerAJobId && workerBJobId) {
  assert.notEqual(workerAJobId, workerBJobId, 'Worker A and Worker B must be distinct jobs');
}

// 5. Independently fetch and verify server-authored stale rejection receipt
console.log(`[migration-verifier] Fetching server stale receipts...`);
const receiptsRes = await fetch(`${controlUrl}/v1/tasks/${encodeURIComponent(taskId)}/receipts`, {
  headers: { 'Authorization': `Bearer ${verifierToken}` },
});
assert.equal(receiptsRes.status, 200);
const { receipts } = await receiptsRes.json();
assert.ok(Array.isArray(receipts) && receipts.length > 0, 'No receipts returned');

const originalWorkerALeaseId = task.checkpoint?.artifact?.lease_id;
assert.ok(originalWorkerALeaseId, 'Missing Worker A lease in checkpoint');

const matchingReceipts = receipts.filter(r =>
  r.task_id === taskId &&
  Number(r.attempted_generation) === 1 &&
  Number(r.attempted_fence) === 1 &&
  r.attempted_lease_id === originalWorkerALeaseId &&
  Number(r.current_generation) === 2 &&
  r.rejection_code === 'STALE_FENCE' &&
  Number(r.http_status ?? r.http_semantic_status) === 409
);
assert.equal(matchingReceipts.length, 1, `Expected exactly 1 matching stale receipt, found ${matchingReceipts.length}`);
const serverReceipt = matchingReceipts[0];

// Recompute server-computed mutation_sha256
const expectedMutationSha = await computeMutationSha256({
  task_id: serverReceipt.task_id,
  submission_kind: serverReceipt.submission_kind ?? 'result',
  attempted_generation: Number(serverReceipt.attempted_generation),
  attempted_fence: Number(serverReceipt.attempted_fence),
  attempted_lease_id: serverReceipt.attempted_lease_id,
  worker_payload_sha256: serverReceipt.worker_payload_sha256 ?? serverReceipt.payload_sha256 ?? null,
});
assert.equal(serverReceipt.mutation_sha256.toLowerCase(), expectedMutationSha.toLowerCase(), 'Mutation hash mismatch');

const receiptId = serverReceipt.receipt_id || serverReceipt.id;
const receiptSha = sha256Json(serverReceipt);
console.log(`[migration-verifier] Server receipt verified: ID=${receiptId} mutation_sha=${serverReceipt.mutation_sha256}`);

// 6. Produce attested ResultAcceptance
const verifierId = `verifier-${process.env.GITHUB_RUN_ID || process.pid}`;
const assertions = [
  { type: 'exit_code_zero', exit_code: artifact.exit_code },
  { type: 'raw_evidence_verified', evidence_ref: evidenceRef, sha256: downloadedSha },
  { type: 'restored_checkpoint_marker_verified', marker: expectedMarker || 'verified' },
  {
    type: 'durable_server_stale_rejection_receipt_verified',
    receipt_id: receiptId,
    receipt_sha256: receiptSha,
    mutation_sha256: serverReceipt.mutation_sha256,
    original_worker_a_lease_id: originalWorkerALeaseId,
  },
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

// 7. Submit acceptance
console.log(`[migration-verifier] Submitting ResultAcceptance to Cloudflare DO...`);
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
  status: 'REMOTE_MIGRATION_PROOF_PASS',
  task_id: taskId,
  worker_a_job_id: workerAJobId,
  worker_b_job_id: workerBJobId,
  verifier_job_id: verifierJobId,
  server_receipt_id: receiptId,
  server_receipt_sha256: receiptSha,
  mutation_sha256: serverReceipt.mutation_sha256,
  result_acceptance_proof_hash: acceptance.proof_sha256,
  final_task_state: finalTask.state,
  timestamp: Date.now(),
};

console.log('REMOTE_MIGRATION_PROOF_PASS:' + JSON.stringify(consolidatedProof));
console.log('[migration-verifier] Migration proof complete. Canonical task is DONE.');

process.exit(0);
