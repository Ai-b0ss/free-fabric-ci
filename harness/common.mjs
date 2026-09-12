import assert from 'node:assert/strict';
import crypto from 'node:crypto';

const enc = new TextEncoder();

export function assertRoleIsolation(role) {
  const forbiddenMaster = ['CLOUDFLARE_API_KEY', 'CLOUDFLARE_EMAIL', 'GH_TOKEN', 'GITHUB_TOKEN_PRIVATE'];
  for (const key of forbiddenMaster) {
    if (process.env[key]) {
      console.error(`SECRET_ISOLATION_VIOLATION: Master secret ${key} leaked into ${role} container!`);
      process.exit(99);
    }
  }

  if (role === 'owner') {
    for (const key of ['FABRIC_RUNNER_TOKEN', 'FABRIC_VERIFIER_TOKEN']) {
      if (process.env[key]) {
        console.error(`SECRET_ISOLATION_VIOLATION: Cross-role token ${key} leaked into owner container!`);
        process.exit(99);
      }
    }
  } else if (role === 'worker') {
    for (const key of ['FABRIC_OWNER_TOKEN', 'FABRIC_VERIFIER_TOKEN']) {
      if (process.env[key]) {
        console.error(`SECRET_ISOLATION_VIOLATION: Cross-role token ${key} leaked into worker container!`);
        process.exit(99);
      }
    }
  } else if (role === 'verifier') {
    for (const key of ['FABRIC_OWNER_TOKEN', 'FABRIC_RUNNER_TOKEN']) {
      if (process.env[key]) {
        console.error(`SECRET_ISOLATION_VIOLATION: Cross-role token ${key} leaked into verifier container!`);
        process.exit(99);
      }
    }
  }
}

export function sha256Json(val) {
  return crypto.createHash('sha256').update(JSON.stringify(val)).digest('hex');
}

export async function computeMutationSha256(canonicalMutation) {
  const mutationBytes = enc.encode(JSON.stringify({
    task_id: String(canonicalMutation.task_id),
    submission_kind: String(canonicalMutation.submission_kind),
    attempted_generation: Number(canonicalMutation.attempted_generation),
    attempted_fence: Number(canonicalMutation.attempted_fence),
    attempted_lease_id: canonicalMutation.attempted_lease_id,
    worker_payload_sha256: canonicalMutation.worker_payload_sha256,
  }));
  const mutationHashBuf = await crypto.subtle.digest('SHA-256', mutationBytes);
  return Array.from(new Uint8Array(mutationHashBuf)).map(b => b.toString(16).padStart(2, '0')).join('');
}

export async function createAttestation({ taskId, generation, targetBackendId, payloadSha256, assertions, verifierSecret, verifierId }) {
  const timestamp = Date.now();
  const proofPayload = {
    verifier_id: verifierId,
    verifier_type: 'TargetComputeVerificationExecutor',
    task_id: taskId,
    generation,
    target_backend_id: targetBackendId,
    passed: true,
    assertions,
    browser_assertions: [],
    payload_sha256: payloadSha256,
    timestamp,
  };
  const computedHash = await crypto.subtle.digest('SHA-256', enc.encode(JSON.stringify(proofPayload)));
  const computedHex = Array.from(new Uint8Array(computedHash)).map(b => b.toString(16).padStart(2, '0')).join('');

  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode(verifierSecret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  const sigBuf = await crypto.subtle.sign('HMAC', key, enc.encode(computedHex));
  const sigHex = Array.from(new Uint8Array(sigBuf)).map(b => b.toString(16).padStart(2, '0')).join('');

  return {
    ...proofPayload,
    proof_sha256: computedHex,
    signature: sigHex,
  };
}

export async function mintProviderTicket(controlUrl, runnerToken, taskId, generation, resourceId) {
  const res = await fetch(`${controlUrl}/v1/provider-tickets`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${runnerToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      task_id: taskId,
      generation,
      resource_id: resourceId,
      ttl_ms: 600000,
    }),
  });
  assert.equal(res.status, 201, `Failed to mint ticket: ${res.status}`);
  const { ticket } = await res.json();
  return ticket;
}

export async function uploadEvidenceArtifact(controlUrl, ticket, taskId, generation, resourceId, workerId, leaseId, fence, rawBytes) {
  const rawSha = crypto.createHash('sha256').update(rawBytes).digest('hex');
  const initRes = await fetch(`${controlUrl}/v1/artifacts/init`, {
    method: 'POST',
    headers: { 'Authorization': `FabricTicket ${ticket}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      task_id: taskId,
      generation,
      resource_id: resourceId,
      worker_id: workerId,
      lease_id: leaseId,
      fence,
      kind: 'evidence',
      total_size: rawBytes.length,
      sha256: rawSha,
      chunk_size: rawBytes.length,
    }),
  });
  assert.equal(initRes.status, 201, `Failed to init artifact: ${initRes.status}`);
  const { artifact_id: artifactId } = await initRes.json();

  const putRes = await fetch(`${controlUrl}/v1/artifacts/${artifactId}/chunks/0`, {
    method: 'PUT',
    headers: { 'Authorization': `FabricTicket ${ticket}`, 'Content-Type': 'application/octet-stream' },
    body: rawBytes,
  });
  assert.ok(putRes.status === 200 || putRes.status === 201, `Failed to put chunk: ${putRes.status}`);

  const commitRes = await fetch(`${controlUrl}/v1/artifacts/${artifactId}/commit`, {
    method: 'POST',
    headers: { 'Authorization': `FabricTicket ${ticket}` },
  });
  assert.ok(commitRes.status === 200 || commitRes.status === 201, `Failed to commit artifact: ${commitRes.status}`);

  return { artifactId, rawSha };
}
