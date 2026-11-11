const OFFICE_HOST = "neo.madewithfelix.app";
const ROUTES = new Map([
  ["/", { path: "/office-public", type: "text/html" }],
  ["/index.html", { path: "/office-public", type: "text/html" }],
  ["/office-calendar.js", { path: "/office-calendar.js", type: "text/javascript" }],
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

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
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
