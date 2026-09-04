from __future__ import annotations

import json
from pathlib import Path
import subprocess


HANDOFF_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "functions"
    / "guancha-extraction-handoff"
    / "index.js"
)


def test_handoff_source_keeps_security_and_event_contract_static() -> None:
    source = HANDOFF_SOURCE.read_text(encoding="utf-8")
    assert "context.extendedContext.userId" in source
    assert "context.extendedContext.accessToken" in source
    assert 'InvocationType: "Event"' in source
    assert 'JSON.stringify({ job_id: jobId })' in source
    assert 'ACL: "private"' in source
    assert 'requiredEnvironment("GUANCHA_DATABASE_URL")' in source
    assert "console.log" not in source
    assert "console.error" not in source


def test_handoff_source_does_not_eagerly_construct_production_clients() -> None:
    source = HANDOFF_SOURCE.read_text(encoding="utf-8")
    assert "let productionHandler;" in source
    assert "createProductionDependencies()" in source
    assert "if (!productionHandler)" in source


def test_handoff_ownership_and_duplicate_contract_with_injected_clients() -> None:
    script = r"""
const {
  HandoffError,
  createHandoffHandler,
  workerPhysicalKey,
} = require(process.argv[1]);

const calls = [];
const dependencies = {
  db: {
    query: async (_sql, values) => ({
      rows: values[1] === "owner-a" ? [{ candidate_image_id: "image-a" }] : [],
    }),
  },
  readSource: async ({ objectKey, accessToken }) => {
    calls.push(["read", objectKey, accessToken]);
    return { data: Buffer.from("synthetic-image"), contentType: "image/png" };
  },
  putDestination: async ({ objectKey, data, contentType }) => {
    calls.push(["put", objectKey, data.toString(), contentType]);
  },
  invokeWorker: async ({ jobId }) => {
    calls.push(["invoke", jobId]);
  },
};
const handler = createHandoffHandler(dependencies);
const event = { job_id: "00000000-0000-4000-8000-000000000001" };

(async () => {
  let missingUidRejected = false;
  try { await handler(event, { extendedContext: { accessToken: "token" } }); }
  catch (error) { missingUidRejected = error instanceof HandoffError; }
  if (!missingUidRejected) throw new Error("missing uid was accepted");

  let wrongOwnerRejected = false;
  try {
    await handler(event, { extendedContext: { userId: "owner-b", accessToken: "token" } });
  } catch (error) { wrongOwnerRejected = error instanceof HandoffError; }
  if (!wrongOwnerRejected) throw new Error("wrong owner was accepted");

  const first = await handler(
    event,
    { extendedContext: { userId: "owner-a", accessToken: "token-a" } },
  );
  const second = await handler(
    event,
    { extendedContext: { userId: "owner-a", accessToken: "token-a" } },
  );
  if (first.status !== "accepted" || second.status !== "accepted") throw new Error("handoff rejected");
  if (JSON.stringify(calls) !== JSON.stringify([
    ["read", "temporary/image-a", "token-a"],
    ["put", "temporary/image-a", "synthetic-image", "image/png"],
    ["invoke", event.job_id],
    ["read", "temporary/image-a", "token-a"],
    ["put", "temporary/image-a", "synthetic-image", "image/png"],
    ["invoke", event.job_id],
  ])) throw new Error("handoff key or idempotency contract changed");
  if (workerPhysicalKey("temporary/image-a", "guancha-prod") !== "guancha-prod/temporary/image-a") {
    throw new Error("physical key contract changed");
  }
  process.stdout.write(JSON.stringify({ missingUidRejected, wrongOwnerRejected, status: first.status }));
})().catch((error) => { process.stderr.write(String(error)); process.exit(1); });
"""
    completed = subprocess.run(
        ["node", "-e", script, str(HANDOFF_SOURCE)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {
        "missingUidRejected": True,
        "wrongOwnerRejected": True,
        "status": "accepted",
    }
