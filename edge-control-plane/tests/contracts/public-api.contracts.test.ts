import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  ArtifactListResponseSchema,
  JobStatusResponseSchema,
  QueryRequestSchema,
  QueryResponseSchema,
  QueuePromptRequestSchema,
  SessionStopRequestSchema,
  SessionStopResponseSchema,
  StreamingQueryRequestSchema,
} from "../../src/contracts/public-api";

const require = createRequire(import.meta.url);

test("query request schema rejects malformed public payloads", () => {
  const result = QueryRequestSchema.safeParse({ question: 42 });
  assert.equal(result.success, false);
});

test("query response schema rejects payloads missing required summary data", () => {
  const result = QueryResponseSchema.safeParse({
    ok: true,
    messages: [],
    session_id: "sess-123",
  });
  assert.equal(result.success, false);
});

test("streaming query schema rejects caller-supplied identity fields", () => {
  const result = StreamingQueryRequestSchema.safeParse({
    question: "hello",
    session_id: "sess-spoofed",
  });
  assert.equal(result.success, false);
});

test("queue prompt schema rejects spoofed user_id fields", () => {
  const result = QueuePromptRequestSchema.safeParse({
    question: "hello",
    user_id: "user-spoofed",
  });
  assert.equal(result.success, false);
});

test("artifact list schema rejects legacy manifest keys", () => {
  const result = ArtifactListResponseSchema.safeParse({
    ok: true,
    job_id: "job-123",
    artifacts: {
      entries: [],
      root: "/data/jobs/job-123",
    },
  });
  assert.equal(result.success, false);
});

test("job status schema requires session identity for ownership enforcement", () => {
  const result = JobStatusResponseSchema.safeParse({
    job_id: "job-123",
    ok: true,
    status: "queued",
  });
  assert.equal(result.success, false);
});

test("session stop request schema rejects unsupported stop modes", () => {
  const result = SessionStopRequestSchema.safeParse({
    mode: "hard-stop",
  });
  assert.equal(result.success, false);
});

test("session stop request schema rejects client-controlled requested_by", () => {
  const result = SessionStopRequestSchema.safeParse({
    mode: "graceful",
    requested_by: "client-spoofed",
  });
  assert.equal(result.success, false);
});

test("session stop response schema rejects invalid status payloads", () => {
  const result = SessionStopResponseSchema.safeParse({
    ok: true,
    session_id: "sess-123",
    status: "stopped",
  });
  assert.equal(result.success, false);
});

test("typedoc configuration stays limited to public contract scope", () => {
  const typedocConfig = JSON.parse(
    readFileSync(
      new URL("../../typedoc.contracts.json", import.meta.url),
      "utf8"
    )
  ) as { entryPoints?: string[] };

  assert.deepEqual(typedocConfig.entryPoints, [
    "src/auth/internal-auth.ts",
    "src/auth/session-auth.ts",
    "src/contracts/public-api.ts",
  ]);
});

test("dependency-cruiser live config keeps required boundary rules", () => {
  const dependencyCruiserConfig = require("../../dependency-cruiser.cjs") as {
    forbidden?: Array<{
      comment?: string;
      from?: { path?: string };
      name?: string;
      severity?: string;
      to?: { path?: string };
    }>;
  };
  const forbidden = dependencyCruiserConfig.forbidden ?? [];
  const byName = new Map(
    forbidden
      .filter((rule) => typeof rule.name === "string")
      .map((rule) => [rule.name as string, rule])
  );

  const foundationRule = byName.get("contracts-are-foundation");
  assert.equal(foundationRule?.severity, "error");
  assert.equal(foundationRule?.from?.path, "^src/contracts/");
  assert.equal(
    foundationRule?.to?.path,
    "^src/(auth|routes|durable-objects|index\\.ts)"
  );

  const authRule = byName.get("auth-does-not-depend-on-transport");
  assert.equal(authRule?.severity, "error");
  assert.equal(authRule?.from?.path, "^src/auth/");
  assert.equal(authRule?.to?.path, "^src/(routes|durable-objects|index\\.ts)");
});

test("dependency-cruiser blocks relative-import boundary bypasses", () => {
  const fixtureRoot = mkdtempSync(join(tmpdir(), "rafiki-depcruise-"));

  try {
    mkdirSync(join(fixtureRoot, "src/contracts"), { recursive: true });
    mkdirSync(join(fixtureRoot, "src/auth"), { recursive: true });

    writeFileSync(
      join(fixtureRoot, "dependency-cruiser.cjs"),
      `module.exports = {
  forbidden: [
    {
      name: "contracts-are-foundation",
      severity: "error",
      from: { path: "^src/contracts/" },
      to: { path: "^src/auth/" }
    }
  ],
  options: {
    tsConfig: { fileName: "tsconfig.json" }
  }
};\n`
    );
    writeFileSync(
      join(fixtureRoot, "tsconfig.json"),
      JSON.stringify(
        {
          compilerOptions: {
            module: "esnext",
            moduleResolution: "bundler",
            target: "es2022",
          },
        },
        null,
        2
      )
    );
    writeFileSync(
      join(fixtureRoot, "src/contracts/index.ts"),
      'import "../auth/token";\nexport const contract = true;\n',
      { encoding: "utf8" }
    );
    writeFileSync(
      join(fixtureRoot, "src/auth/token.ts"),
      "export const token = 'secret';\n",
      { encoding: "utf8" }
    );

    let exitCode = 0;
    try {
      execFileSync(
        "./node_modules/.bin/depcruise",
        ["--config", "dependency-cruiser.cjs", "src"],
        {
          cwd: fixtureRoot,
          encoding: "utf8",
          stdio: "pipe",
        }
      );
    } catch (error) {
      exitCode =
        typeof error === "object" &&
        error !== null &&
        "status" in error &&
        typeof error.status === "number"
          ? error.status
          : 1;
    }

    assert.notEqual(exitCode, 0);
  } finally {
    rmSync(fixtureRoot, { force: true, recursive: true });
  }
});
