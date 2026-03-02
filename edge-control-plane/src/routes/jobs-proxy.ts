import {
  buildArtifactAccessToken,
  buildInternalAuthToken,
} from "../auth/internal-auth";
import { authenticateClientRequest } from "../auth/session-auth";
import type { Env, JobEventMessage, JobStatusResponse } from "../types";

const ARTIFACT_PREFIX = "/artifacts/";
const JOB_PATH_REGEX = /^\/jobs\/([^/]+)(\/.*)?$/;

type JobRouteAuth = Awaited<ReturnType<typeof authenticateClientRequest>>;
interface JobRouteEventAuth {
  session_id: string;
  tenant_id?: string;
  user_id?: string;
}

interface JobRouteMode {
  isArtifactDownload: boolean;
  isArtifactList: boolean;
  isJobStatusRead: boolean;
  needsOwnershipCheck: boolean;
}

interface JobOwnershipFetchResult {
  ownership?: JobStatusResponse;
  response?: Response;
}

interface JobEventScheduler {
  scheduleJobEvent: (auth: JobRouteEventAuth, message: JobEventMessage) => void;
}

function parseJobRoute(
  path: string
): { jobId: string; subpath: string } | null {
  const match = path.match(JOB_PATH_REGEX);
  if (!match) {
    return null;
  }
  return { jobId: match[1], subpath: match[2] || "" };
}

function getJobRouteMode(method: string, subpath: string): JobRouteMode {
  const isArtifactList =
    method === "GET" && (subpath === "/artifacts" || subpath === "/artifacts/");
  const isArtifactDownload =
    method === "GET" &&
    subpath.startsWith(ARTIFACT_PREFIX) &&
    subpath.length > ARTIFACT_PREFIX.length;
  const isJobStatusRead = method === "GET" && subpath === "";
  return {
    isArtifactDownload,
    isArtifactList,
    isJobStatusRead,
    needsOwnershipCheck:
      isArtifactList || isArtifactDownload || isJobStatusRead,
  };
}

function buildModalProxyHeaders(options: {
  auth: JobRouteAuth;
  authToken: string;
  artifactAccessToken?: string;
}): Record<string, string> {
  return {
    "Content-Type": "application/json",
    "X-Internal-Auth": options.authToken,
    "X-Session-Id": options.auth.session_id,
    "X-Session-Key": options.auth.session_key || "",
    "X-Tenant-Id": options.auth.tenant_id || "",
    "X-User-Id": options.auth.user_id || "",
    ...(options.artifactAccessToken
      ? { "X-Artifact-Access-Token": options.artifactAccessToken }
      : {}),
  };
}

async function fetchJobOwnership(options: {
  auth: JobRouteAuth;
  authToken: string;
  env: Env;
  jobId: string;
}): Promise<JobOwnershipFetchResult> {
  const response = await fetch(
    `${options.env.MODAL_API_BASE_URL}/jobs/${options.jobId}`,
    {
      method: "GET",
      headers: buildModalProxyHeaders({
        auth: options.auth,
        authToken: options.authToken,
      }),
    }
  );
  if (!response.ok) {
    return {
      response: new Response(await response.text(), {
        status: response.status,
        headers: { "Content-Type": "application/json" },
      }),
    };
  }
  return { ownership: (await response.json()) as JobStatusResponse };
}

function validateOwnershipScope(
  ownership: JobStatusResponse,
  auth: JobRouteAuth
): Response | null {
  if (ownership.session_id && ownership.session_id !== auth.session_id) {
    return new Response(
      JSON.stringify({ ok: false, error: "Job session mismatch" }),
      {
        status: 403,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
  if (auth.user_id && ownership.user_id && ownership.user_id !== auth.user_id) {
    return new Response(
      JSON.stringify({ ok: false, error: "Job user mismatch" }),
      {
        status: 403,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
  if (
    auth.tenant_id &&
    ownership.tenant_id &&
    ownership.tenant_id !== auth.tenant_id
  ) {
    return new Response(
      JSON.stringify({ ok: false, error: "Job tenant mismatch" }),
      {
        status: 403,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
  return null;
}

async function buildArtifactDownloadToken(options: {
  auth: JobRouteAuth;
  env: Env;
  isArtifactDownload: boolean;
  jobId: string;
  subpath: string;
}): Promise<{ response?: Response; token?: string }> {
  if (!options.isArtifactDownload) {
    return {};
  }

  let artifactPath: string;
  try {
    artifactPath = decodeURIComponent(
      options.subpath.slice(ARTIFACT_PREFIX.length)
    );
  } catch {
    return {
      response: new Response(
        JSON.stringify({ ok: false, error: "Invalid artifact path encoding" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      ),
    };
  }

  return {
    token: await buildArtifactAccessToken({
      secret: options.env.INTERNAL_AUTH_SECRET,
      sessionId: options.auth.session_id,
      jobId: options.jobId,
      artifactPath,
      ttlMs: 120_000,
    }),
  };
}

function scheduleJobStatusEventFromResponse(
  options: {
    auth: JobRouteAuth;
    ctx: ExecutionContext;
    jobId: string;
    response: Response;
  } & JobEventScheduler
): void {
  if (!options.response.ok) {
    return;
  }
  const contentType = options.response.headers.get("Content-Type") || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    return;
  }

  const clone = options.response.clone();
  options.ctx.waitUntil(
    (async () => {
      try {
        const payload = (await clone.json()) as JobStatusResponse;
        const jobEvent: JobEventMessage = {
          type: "job_status",
          session_id: payload.session_id || options.auth.session_id,
          timestamp: Date.now(),
          data: {
            job_id: payload.job_id || options.jobId,
            status: payload.status,
            user_id: payload.user_id || options.auth.user_id,
            tenant_id: payload.tenant_id || options.auth.tenant_id,
            payload,
          },
        };
        options.scheduleJobEvent(options.auth, jobEvent);
      } catch (error) {
        console.warn("Failed to publish job_status event", error);
      }
    })()
  );
}

export async function handleJobsEndpoint(
  options: {
    ctx: ExecutionContext;
    env: Env;
    path: string;
    request: Request;
  } & JobEventScheduler
): Promise<Response> {
  const parsedRoute = parseJobRoute(options.path);
  if (!parsedRoute) {
    return new Response("Invalid job path", { status: 400 });
  }

  const { jobId, subpath } = parsedRoute;
  const mode = getJobRouteMode(options.request.method, subpath);

  const auth = await authenticateClientRequest({
    request: options.request,
    env: options.env,
    sessionId: new URL(options.request.url).searchParams.get("session_id"),
    sessionKey: new URL(options.request.url).searchParams.get("session_key"),
    userId: new URL(options.request.url).searchParams.get("user_id"),
    tenantId: new URL(options.request.url).searchParams.get("tenant_id"),
  });

  const modalUrl = `${options.env.MODAL_API_BASE_URL}${options.path}`;
  const authToken = await buildInternalAuthToken(
    options.env.INTERNAL_AUTH_SECRET
  );
  let ownership: JobStatusResponse | undefined;

  if (mode.needsOwnershipCheck) {
    const ownershipResult = await fetchJobOwnership({
      auth,
      authToken,
      env: options.env,
      jobId,
    });
    if (ownershipResult.response) {
      return ownershipResult.response;
    }
    ownership = ownershipResult.ownership;
    if (ownership) {
      const ownershipValidationError = validateOwnershipScope(ownership, auth);
      if (ownershipValidationError) {
        return ownershipValidationError;
      }
    }
  }

  if (mode.isJobStatusRead && ownership) {
    return new Response(JSON.stringify(ownership), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  const artifactToken = await buildArtifactDownloadToken({
    auth,
    env: options.env,
    isArtifactDownload: mode.isArtifactDownload,
    jobId,
    subpath,
  });
  if (artifactToken.response) {
    return artifactToken.response;
  }

  const response = await fetch(modalUrl, {
    method: options.request.method,
    headers: buildModalProxyHeaders({
      auth,
      authToken,
      artifactAccessToken: artifactToken.token,
    }),
  });

  scheduleJobStatusEventFromResponse({
    auth,
    ctx: options.ctx,
    jobId,
    response,
    scheduleJobEvent: options.scheduleJobEvent,
  });

  return response;
}
