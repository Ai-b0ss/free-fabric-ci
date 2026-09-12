import assert from 'node:assert/strict';
import { assertRoleIsolation } from './common.mjs';

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
console.log(`[forged-verifier-reject] Fetching canonical task ${taskId}...`);
const taskRes = await fetch(`${controlUrl}/v1/tasks/${encodeURIComponent(taskId)}`, {
  headers: { 'Authorization': `Bearer ${verifierToken}` },
});
assert.equal(taskRes.status, 200);
const { task } = await taskRes.json();
assert.equal(task.generation, 1);

const candidate = task.result_candidate;
assert.ok(candidate, 'Missing candidate');
const artifact = candidate.artifact;
const evidenceRef = artifact.execution_evidence_ref;
assert.ok(evidenceRef, 'Missing execution_evidence_ref');

// 2. Independently download raw evidence and inspect bytes
console.log(`[forged-verifier-reject] Downloading evidence ${evidenceRef}...`);
const chunkRes = await fetch(`${controlUrl}/v1/artifacts/${evidenceRef}/chunks/0`, {
  headers: { 'Authorization': `Bearer ${verifierToken}` },
});
assert.equal(chunkRes.status, 200);
const downloadedBytes = Buffer.from(await chunkRes.arrayBuffer());
const rawStdout = downloadedBytes.toString('utf8');

assert.ok(!rawStdout.includes(honestMarker), `Evidence must contradict honest marker! Output was: ${rawStdout}`);
console.log(`[forged-verifier-reject] Contradiction detected: evidence lacks expected marker.`);

// 3. Reject candidate -> Generation bumps to 2, task state becomes READY
console.log(`[forged-verifier-reject] Rejecting candidate on control plane...`);
const rejectRes = await fetch(`${controlUrl}/v1/tasks/reject`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${verifierToken}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    task_id: taskId,
    generation: 1,
    feedback: `Contradictory evidence: output does not contain ${honestMarker}`,
  }),
});
assert.equal(rejectRes.status, 200, `Reject failed: ${rejectRes.status}`);
const rejectData = await rejectRes.json();

assert.equal(rejectData.task.generation, 2, 'Generation must bump to 2');
assert.equal(rejectData.task.state, 'READY', 'State must be READY');

console.log('FORGED_REJECT_PASS:' + JSON.stringify({
  status: 'FORGED_CANDIDATE_REJECTED',
  task_id: taskId,
  rejected_generation: 1,
  new_generation: 2,
  new_state: 'READY',
}));

process.exit(0);
