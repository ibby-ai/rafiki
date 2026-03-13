/**
 * Shared public Worker route and CORS policy.
 *
 * Keeping this policy in a pure module lets contract tests verify the public
 * surface without importing Durable Object runtime shims.
 */

const SESSION_QUEUE_ITEM_REGEX = /^\/queue\/[^/]+$/;

export function buildCorsHeaders(): Record<string, string> {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Max-Age": "86400",
  };
}

export function getPublicSessionRoutePolicy(subpath: string): {
  allowedMethods: string[];
  forwardPath: string;
} | null {
  switch (subpath) {
    case "/messages":
      return { allowedMethods: ["GET"], forwardPath: subpath };
    case "/queue":
      return {
        allowedMethods: ["GET", "POST", "DELETE"],
        forwardPath: subpath,
      };
    case "/state":
      return { allowedMethods: ["GET"], forwardPath: subpath };
    case "/stop":
      return { allowedMethods: ["GET", "POST"], forwardPath: subpath };
    default:
      if (SESSION_QUEUE_ITEM_REGEX.test(subpath)) {
        return { allowedMethods: ["DELETE"], forwardPath: subpath };
      }
      return null;
  }
}
