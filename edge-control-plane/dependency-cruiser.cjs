"use strict";

/** @type {import('dependency-cruiser').IConfiguration} */
module.exports = {
  forbidden: [
    {
      name: "contracts-are-foundation",
      comment:
        "Contracts must stay detached from transport and auth implementation modules.",
      severity: "error",
      from: { path: "^src/contracts/" },
      to: { path: "^src/(auth|routes|durable-objects|index\\.ts)" },
    },
    {
      name: "auth-does-not-depend-on-transport",
      comment:
        "Auth helpers may depend on shared types/contracts, but not transport orchestration code.",
      severity: "error",
      from: { path: "^src/auth/" },
      to: { path: "^src/(routes|durable-objects|index\\.ts)" },
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
    tsConfig: { fileName: "tsconfig.json" },
  },
};
