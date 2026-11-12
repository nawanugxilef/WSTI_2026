import assert from "node:assert/strict";
import { test } from "node:test";
import worker from "./neo-office.mjs";

const office = "https://neo.madewithfelix.app";
const demo = "https://neo-wsti-demo.madewithfelix.app";
const enabled = { NEO_WSTI_DEMO_ORIGIN: demo };

test("the demo opt-in redirects only the Office demo entry routes", async () => {
  for (const path of ["/demo", "/demo/"]) {
    for (const method of ["GET", "HEAD"]) {
      const response = await worker.fetch(new Request(office + path, { method }), enabled);
      assert.equal(response.status, 302);
      assert.equal(response.headers.get("Location"), `${demo}/demo`);
      assert.equal(response.headers.get("Cache-Control"), "no-store");
    }
    const write = await worker.fetch(new Request(office + path, { method: "POST" }), enabled);
    assert.equal(write.status, 405);
  }
  assert.equal((await worker.fetch(new Request(office + "/demo"), {})).status, 404);
  assert.equal((await worker.fetch(new Request(office + "/demo"), { NEO_WSTI_DEMO_ORIGIN: "https://other.example" })).status, 404);
  assert.equal((await worker.fetch(new Request(office + "/demo/api/session"), enabled)).status, 404);
  assert.equal((await worker.fetch(new Request(office + "/api/dashboard"), enabled)).status, 401);
});

test("the opted-in Office navigation appends Try Neo alongside Dashboard", async (t) => {
  const page = '<nav data-neo-office-toolbar><a href="/dashboard">Dashboard</a></nav>';
  const officeEnv = { NEO_OFFICE_ORIGIN: "https://office.example" };
  t.mock.method(globalThis, "fetch", async (url) => {
    assert.equal(url, "https://office.example/office-public");
    return new Response(page, { headers: { "Content-Type": "text/html" } });
  });

  let rewrites = 0;
  globalThis.HTMLRewriter = class {
    on(selector, handler) {
      assert.equal(selector, "nav[data-neo-office-toolbar]");
      this.handler = handler;
      return this;
    }

    async transform(response) {
      rewrites++;
      let appended = "";
      this.handler.element({ append(value, options) {
        assert.deepEqual(options, { html: true });
        appended = value;
      } });
      const html = await response.text();
      return new Response(html.replace("</nav>", appended + "</nav>"), response);
    }
  };
  t.after(() => delete globalThis.HTMLRewriter);

  const before = await worker.fetch(new Request(office + "/"), officeEnv);
  assert.equal(await before.text(), page);
  assert.equal(before.headers.get("Cache-Control"), "public, max-age=60");
  assert.equal(rewrites, 0);

  for (const path of ["/", "/index.html"]) {
    const response = await worker.fetch(new Request(office + path), { ...officeEnv, ...enabled });
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.equal(await response.text(), page.replace("</nav>", '<a href="/demo">Try Neo</a></nav>'));
  }

  const head = await worker.fetch(new Request(office + "/", { method: "HEAD" }), { ...officeEnv, ...enabled });
  assert.equal(head.status, 200);
  assert.equal(head.body, null);
  assert.equal(head.headers.get("Cache-Control"), "no-store");
  assert.equal(rewrites, 2);
});
