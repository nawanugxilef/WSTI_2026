"use strict";

const OFFLINE_PAGE = `<!doctype html>
<html lang="en-AU">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#f2f5f3">
<title>Neo Dashboard</title>
<style>
  body { margin: 2rem auto; max-width: 26rem; padding: 0 1rem; background: #f2f5f3; color: #172824; font: 1rem system-ui; }
  h1 { font-size: 1.25rem; }
  a { color: #225e52; }
</style>
<main>
  <h1>You’re offline</h1>
  <p>Reconnect to open your Neo ledger.</p>
  <a href="/dashboard">Try again</a>
</main>
</html>`;

self.addEventListener("install", (event) => event.waitUntil(self.skipWaiting()));
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || event.request.mode !== "navigate"
    || url.origin !== self.location.origin || !/^\/dashboard\/?$/.test(url.pathname)) return;

  event.respondWith(fetch(event.request).catch(() => new Response(OFFLINE_PAGE, {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
    },
  })));
});
