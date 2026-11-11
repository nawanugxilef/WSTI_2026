const OFFICE_HOST = "neo.madewithfelix.app";
const DASHBOARD_ORIGIN = "https://" + OFFICE_HOST;
const READ = ["GET", "HEAD"];
const DASHBOARD_ROUTES = new Map([
  ["/dashboard", { type: "text/html", methods: READ }],
  ["/dashboard/", { type: "text/html", methods: READ }],
  ["/dashboard.css", { type: "text/css", methods: READ }],
  ["/dashboard.js", { type: "text/javascript", methods: READ }],
  ["/api/dashboard", { type: "application/json", methods: READ, session: true }],
  ["/api/export.csv", { type: "text/csv", methods: READ, session: true }],
  ["/api/dashboard/session", { type: "application/json", methods: ["POST", "DELETE"], maxBytes: 1024, login: true }],
  ["/api/dashboard/intake", { type: "application/json", methods: ["POST"], maxBytes: 10 * 1024 * 1024, session: true }],
  ["/api/dashboard/reward", { type: "application/json", methods: ["POST"], maxBytes: 2048, session: true }],
]);
const ROUTES = new Map([
  ["/", { path: "/office-public", type: "text/html" }],
  ["/index.html", { path: "/office-public", type: "text/html" }],
  ["/office-calendar.js", { path: "/office-calendar.js", type: "text/javascript" }],
  ["/office-board.js", { path: "/office-board.js", type: "text/javascript" }],
  ["/api/office/receipt-progress", { path: "/api/office/receipt-progress", type: "application/json" }],
]);

const CONTENT_SECURITY_POLICY = [
  "default-src 'self' data: blob:",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' data: https://fonts.gstatic.com",
  "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://unpkg.com https://cdn.jsdelivr.net",
  "img-src 'self' data: blob: https:",
  "form-action 'none'",
  "frame-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
].join("; ");

const DASHBOARD_CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self' https://fonts.googleapis.com",
  "font-src https://fonts.gstatic.com",
  "connect-src 'self'",
  "img-src 'self' data:",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "base-uri 'none'",
].join("; ");

function dashboardHeaders() {
  return new Headers({
    "Cache-Control": "private, no-store",
    "Content-Security-Policy": DASHBOARD_CSP,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
  });
}

function dashboardError(status, message, head = false, allow = null) {
  const headers = dashboardHeaders();
  headers.set("Content-Type", "application/json; charset=utf-8");
  if (allow) headers.set("Allow", allow.join(", "));
  return new Response(head ? null : JSON.stringify({ error: message }), { status, headers });
}

async function serveDashboard(request, url, route, env) {
  const head = request.method === "HEAD";
  if (!route.methods.includes(request.method)) {
    return dashboardError(405, "Method not allowed.", head, route.methods);
  }
  const write = !READ.includes(request.method);
  if (write && request.headers.get("Origin") !== DASHBOARD_ORIGIN) {
    return dashboardError(403, "Invalid request origin.");
  }
  const cookie = /(?:^|;\s*)(__Host-neo-dashboard=[A-Za-z0-9_-]{43})(?:;|$)/
    .exec(request.headers.get("Cookie") || "")?.[1];
  if (route.session && !cookie) return dashboardError(401, "Sign in to open the dashboard.", head);

  try {
    const upstreamUrl = new URL(url.pathname, env.NEO_DASHBOARD_ORIGIN);
    if (upstreamUrl.protocol !== "https:" || upstreamUrl.origin === url.origin || upstreamUrl.username || upstreamUrl.password) {
      return dashboardError(503, "Dashboard connection is unavailable.", head);
    }
    if (route.session && !write) upstreamUrl.search = url.search;
    const headers = new Headers({ Accept: route.type });
    if (write) headers.set("Origin", DASHBOARD_ORIGIN);
    if (cookie && (route.session || route.login)) headers.set("Cookie", cookie);
    let body;
    if (request.method === "POST") {
      if (request.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase() !== "application/json") {
        return dashboardError(415, "Expected a JSON request.");
      }
      if (Number(request.headers.get("Content-Length")) > route.maxBytes) {
        return dashboardError(413, "Request is too large.");
      }
      body = await request.arrayBuffer();
      if (body.byteLength > route.maxBytes) return dashboardError(413, "Request is too large.");
      headers.set("Content-Type", "application/json");
    }
    const upstream = await fetch(upstreamUrl.href, {
      method: head ? "GET" : request.method,
      headers,
      body,
      redirect: "manual",
      signal: AbortSignal.timeout(url.pathname === "/api/dashboard/intake" ? 90_000 : 10_000),
      cf: { cacheTtl: 0 },
    });
    const type = upstream.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase();
    const validType = upstream.status === 204 && route.login
      || (upstream.ok ? type === route.type : type === "application/json");
    if (!validType || upstream.status >= 500 && upstream.status !== 503 || upstream.status >= 300 && upstream.status < 400) {
      return dashboardError(502, "Dashboard connection is unavailable.", head);
    }
    const responseHeaders = dashboardHeaders();
    if (upstream.status !== 204) responseHeaders.set("Content-Type", type + "; charset=utf-8");
    for (const key of ["Content-Disposition", "Retry-After"]) {
      if (upstream.headers.has(key)) responseHeaders.set(key, upstream.headers.get(key));
    }
    if (route.login && upstream.headers.has("Set-Cookie")) {
      responseHeaders.set("Set-Cookie", upstream.headers.get("Set-Cookie"));
    }
    return new Response(head || upstream.status === 204 ? null : upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    return dashboardError(502, "Dashboard connection is unavailable.", head);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const dashboardRoute = DASHBOARD_ROUTES.get(url.pathname);
    if (url.hostname === OFFICE_HOST && dashboardRoute) {
      return serveDashboard(request, url, dashboardRoute, env);
    }
    const route = ROUTES.get(url.pathname);
    if (url.hostname !== OFFICE_HOST || !route) {
      return new Response("Not found", { status: 404 });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
    }

    try {
      const upstream = await fetch(new URL(route.path, env.NEO_OFFICE_ORIGIN).href, {
        headers: { Accept: route.type },
        redirect: "manual",
        signal: AbortSignal.timeout(5000),
        cf: { cacheTtl: 0 },
      });
      const type = upstream.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase();
      if (!upstream.ok || type !== route.type) {
        return new Response("Neo Office is temporarily unavailable", { status: 502 });
      }

      return new Response(request.method === "HEAD" ? null : upstream.body, {
        headers: {
          "Content-Type": `${route.type}; charset=utf-8`,
          "Cache-Control": route.type === "application/json" ? "no-store" : "public, max-age=60",
          "Content-Security-Policy": CONTENT_SECURITY_POLICY,
          "Referrer-Policy": "no-referrer",
          "X-Content-Type-Options": "nosniff",
        },
      });
    } catch {
      return new Response("Neo Office is temporarily unavailable", { status: 502 });
    }
  },
};
