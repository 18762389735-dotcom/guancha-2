"use strict";

const DEFAULT_REGION = "ap-shanghai";
const DEFAULT_WORKER_FUNCTION = "guancha-extraction-worker";
const DEFAULT_WORKER_NAMESPACE = "guancha-d0glws9y52bc87082";
const DEFAULT_COS_PREFIX = "guancha-prod";
const CLOUDBASE_DOMESTIC_REGIONS = new Set(["ap-shanghai", "ap-guangzhou"]);

class HandoffError extends Error {
  constructor(message) {
    super(message);
    this.name = "HandoffError";
  }
}

function requiredEnvironment(name) {
  const value = process.env[name];
  if (typeof value !== "string" || value.trim() === "") {
    throw new HandoffError("handoff configuration is incomplete");
  }
  return value.trim();
}

function cloudBaseOrigin(envId, region = DEFAULT_REGION) {
  const normalizedRegion = String(region || DEFAULT_REGION).trim().toLowerCase();
  if (!CLOUDBASE_DOMESTIC_REGIONS.has(normalizedRegion)) {
    throw new HandoffError("handoff CloudBase region is unsupported");
  }
  return `https://${envId}.api.tcloudbasegateway.com`;
}

function normalizeLogicalKey(objectKey) {
  const value = String(objectKey || "").trim().replace(/^\/+|\/+$/g, "");
  const parts = value.split("/");
  if (!value || value.includes("\\") || parts.some((part) => !part || part === "." || part === "..")) {
    throw new HandoffError("handoff object key is invalid");
  }
  return value;
}

function workerPhysicalKey(logicalKey, prefix = DEFAULT_COS_PREFIX) {
  const normalizedPrefix = normalizeLogicalKey(prefix);
  return `${normalizedPrefix}/${normalizeLogicalKey(logicalKey)}`;
}

function cloudObjectId(envId, logicalKey) {
  return `cloud://${envId}.bucket/${normalizeLogicalKey(logicalKey)}`;
}

function validateJobId(event) {
  if (!event || typeof event !== "object" || Array.isArray(event)) {
    throw new HandoffError("handoff event must contain only job_id");
  }
  const keys = Object.keys(event);
  if (keys.length !== 1 || keys[0] !== "job_id" || typeof event.job_id !== "string") {
    throw new HandoffError("handoff event must contain only job_id");
  }
  const jobId = event.job_id.trim();
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(jobId)) {
    throw new HandoffError("handoff job_id must be a UUID");
  }
  return jobId;
}

function callerUidFromContext(context) {
  const uid = context && context.extendedContext && context.extendedContext.userId;
  if (typeof uid !== "string" || uid.trim() === "") {
    throw new HandoffError("authenticated caller is required");
  }
  return uid.trim();
}

function callerAccessTokenFromContext(context) {
  const token = context && context.extendedContext && context.extendedContext.accessToken;
  if (typeof token !== "string" || token.trim() === "") {
    throw new HandoffError("authenticated CloudBase access token is required");
  }
  return token.trim();
}

async function readCloudBaseObject({ envId, region, accessToken, objectKey, fetchImpl = fetch }) {
  const logicalKey = normalizeLogicalKey(objectKey);
  const origin = cloudBaseOrigin(envId, region);
  const infoResponse = await fetchImpl(`${origin}/v1/storages/get-objects-download-info`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify([{ cloudObjectId: cloudObjectId(envId, logicalKey) }]),
  });
  if (!infoResponse.ok) {
    throw new HandoffError("CloudBase source object lookup failed");
  }
  const items = await infoResponse.json();
  const item = Array.isArray(items) ? items[0] : null;
  if (!item || item.code || typeof item.downloadUrl !== "string" || !item.downloadUrl) {
    throw new HandoffError("CloudBase source object lookup failed");
  }
  const downloadResponse = await fetchImpl(item.downloadUrl);
  if (!downloadResponse.ok) {
    throw new HandoffError("CloudBase source object read failed");
  }
  return {
    data: Buffer.from(await downloadResponse.arrayBuffer()),
    contentType: downloadResponse.headers.get("content-type") || "application/octet-stream",
  };
}

async function findOwnedExtractionJob(pool, { jobId, callerUid }) {
  const result = await pool.query(
    `select j.id, j.candidate_image_id
       from analysis_jobs j
       join candidates c on c.id = j.candidate_id
       join selection_sessions s on s.id = c.selection_session_id
       join app_users u on u.id = s.user_id
      where j.id = $1
        and j.job_kind = 'extraction'
        and u.cloudbase_user_id = $2`,
    [jobId, callerUid],
  );
  const row = result.rows && result.rows[0];
  if (!row || !row.candidate_image_id) {
    throw new HandoffError("handoff job was not found for the authenticated owner");
  }
  return { candidateImageId: String(row.candidate_image_id) };
}

function createProductionDependencies() {
  const COS = require("cos-nodejs-sdk-v5");
  const tencentcloud = require("tencentcloud-sdk-nodejs");
  const { Pool } = require("pg");
  const databaseUrl = requiredEnvironment("GUANCHA_DATABASE_URL");
  const envId = requiredEnvironment("CLOUDBASE_ENV_ID");
  const region = process.env.CLOUDBASE_REGION || DEFAULT_REGION;
  const secretId = requiredEnvironment("TENCENTCLOUD_SECRETID");
  const secretKey = requiredEnvironment("TENCENTCLOUD_SECRETKEY");
  const sessionToken = (process.env.TENCENTCLOUD_SESSIONTOKEN || "").trim();
  const cosBucket = requiredEnvironment("GUANCHA_PRIVATE_STORAGE_COS_BUCKET");
  const cosPrefix = process.env.GUANCHA_PRIVATE_STORAGE_COS_PREFIX || DEFAULT_COS_PREFIX;
  const workerFunction = process.env.GUANCHA_EXTRACTION_FUNCTION_NAME || DEFAULT_WORKER_FUNCTION;
  const workerNamespace = process.env.GUANCHA_EXTRACTION_FUNCTION_NAMESPACE || DEFAULT_WORKER_NAMESPACE;
  const ScfClient = tencentcloud.scf.v20180416.Client;

  const pool = new Pool({ connectionString: databaseUrl });
  const cos = new COS({
    SecretId: secretId,
    SecretKey: secretKey,
    SecurityToken: sessionToken || undefined,
  });
  const scf = new ScfClient({
    credential: {
      secretId,
      secretKey,
      ...(sessionToken ? { token: sessionToken } : {}),
    },
    region,
  });

  return {
    db: { query: (text, values) => pool.query(text, values) },
    readSource: ({ objectKey, accessToken }) => readCloudBaseObject({
      envId,
      region,
      accessToken,
      objectKey,
    }),
    putDestination: ({ objectKey, data, contentType }) => new Promise((resolve, reject) => {
      cos.putObject({
        Bucket: cosBucket,
        Region: region,
        Key: workerPhysicalKey(objectKey, cosPrefix),
        Body: data,
        ContentType: contentType,
        ACL: "private",
      }, (error, result) => (error ? reject(new HandoffError("private COS write failed")) : resolve(result)));
    }),
    invokeWorker: ({ jobId }) => scf.Invoke({
      FunctionName: workerFunction,
      Namespace: workerNamespace,
      InvocationType: "Event",
      ClientContext: JSON.stringify({ job_id: jobId }),
    }),
  };
}

function createHandoffHandler(dependencies) {
  const { db, readSource, putDestination, invokeWorker } = dependencies;
  if (!db || typeof db.query !== "function" || typeof readSource !== "function" ||
      typeof putDestination !== "function" || typeof invokeWorker !== "function") {
    throw new HandoffError("handoff dependencies are incomplete");
  }
  return async function handoffHandler(event, context) {
    const jobId = validateJobId(event);
    const callerUid = callerUidFromContext(context);
    const accessToken = callerAccessTokenFromContext(context);
    const job = await findOwnedExtractionJob(db, { jobId, callerUid });
    const logicalKey = `temporary/${job.candidateImageId}`;
    const source = await readSource({ objectKey: logicalKey, accessToken });
    await putDestination({
      objectKey: logicalKey,
      data: source.data,
      contentType: source.contentType,
    });
    await invokeWorker({ jobId });
    return { job_id: jobId, status: "accepted" };
  };
}

let productionHandler;

async function main_handler(event, context) {
  // Resolve production clients only when the function is invoked.  This keeps
  // module import/startup independent from request credentials and makes the
  // deployment artifact safe to inspect locally without secrets.
  if (!productionHandler) {
    productionHandler = createHandoffHandler(createProductionDependencies());
  }
  return productionHandler(event, context);
}

module.exports = {
  HandoffError,
  callerUidFromContext,
  cloudObjectId,
  createHandoffHandler,
  findOwnedExtractionJob: findOwnedExtractionJob,
  main_handler,
  normalizeLogicalKey,
  validateJobId,
  workerPhysicalKey,
};
