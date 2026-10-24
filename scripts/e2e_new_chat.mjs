// Focused navigation regression. Requires a running demo and Playwright (see TESTING.md).
// Conversation responses are intercepted in this browser; no real runs are created/cancelled.
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
const { chromium } = await import(process.env.PODOS_PLAYWRIGHT_MODULE ?? "playwright");
const WEB = process.env.PODOS_WEB ?? "http://localhost:3400";
const API = process.env.PODOS_API ?? "http://localhost:8400";
const OUT = process.argv[2] ?? "var/new-chat-qa";
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const checks = [], errors = [], threads = new Map(), runs = new Map();
let cancellations = 0, slowAccept = null;
try {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const login = await ctx.request.post(`${API}/v1/auth/dev-login`, { data: { subject_id: "yasir.khalid" } });
  assert.ok(login.ok(), "Demo login failed");
  const page = await ctx.newPage();
  page.on("pageerror", error => errors.push(error.message));
  await page.route(`${API}/v1/pods/pod_alpha/threads`, async route => {
    if (route.request().method() === "POST") {
      const id = randomUUID();
      threads.set(id, { thread_id: id, title: `Navigation fixture ${threads.size + 1}`, messages: [], case_id: null, active_run: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() });
      return route.fulfill({ json: { thread_id: id } });
    }
    await route.fulfill({ json: [...threads.values()].map(t => ({ ...t, active_run_id: t.active_run?.run_id })) });
  });
  await page.route(`${API}/v1/threads/*`, route => {
    const id = new URL(route.request().url()).pathname.split("/").at(-1);
    return threads.has(id) ? route.fulfill({ json: threads.get(id) }) : route.fallback();
  });
  await page.route(`${API}/v1/threads/*/runs`, async route => {
    const id = new URL(route.request().url()).pathname.split("/")[3];
    const thread = threads.get(id), runId = randomUUID(), { message } = route.request().postDataJSON();
    let finish;
    const done = new Promise(resolve => { finish = resolve; });
    runs.set(runId, { thread, message, done, finish });
    thread.messages.push({ message_id: randomUUID(), role: "user", run_id: runId, created_at: new Date().toISOString(), content: { text: message } });
    thread.active_run = { run_id: runId };
    if (slowAccept) await slowAccept;
    await route.fulfill({ json: { run_id: runId } });
  });
  await page.route(`${API}/v1/runs/*/events`, async route => {
    const runId = new URL(route.request().url()).pathname.split("/")[3];
    const run = runs.get(runId);
    await run.done;
    const timestamp = new Date().toISOString();
    const events = [{ type: "run.started" }, { type: "agent.selected", agent_id: "equity-research", mode: "auto" }, { type: "message.delta", text: `Answer to ${run.message}` }, { type: "run.completed", citations: [] }];
    await route.fulfill({ contentType: "text/event-stream", body: events.map((event, index) => `event: ${event.type}\ndata: ${JSON.stringify({ ...event, run_id: runId, sequence: index + 1, timestamp })}\n\n`).join("") });
  });
  await page.route(`${API}/v1/runs/*/cancel`, route => { cancellations++; return route.fulfill({ json: {} }); });
  const input = page.getByRole("textbox", { name: "Message input", exact: true });
  const newChat = page.getByRole("link", { name: /^New chat/ }).first();
  async function empty() {
    await page.waitForFunction(() => [...document.querySelectorAll('[aria-label="Message input"]')].some(el => el.checkVisibility() && el.value === "") && ![...document.querySelectorAll('[data-role="user"]')].some(el => el.checkVisibility()));
    assert.equal(await page.locator('[data-role="assistant"]').filter({ visible: true }).count(), 0);
    assert.ok((await page.getByRole("button", { name: "Choose agent", exact: true }).innerText()).includes("Auto"));
  }
  async function fresh(action) {
    const before = page.url();
    await action();
    await page.waitForURL(url => url.searchParams.has("draft") && url.href !== before);
    await empty();
  }
  function finish(runId) {
    const run = runs.get(runId);
    run.thread.active_run = null;
    run.thread.messages.push({ message_id: randomUUID(), run_id: runId, role: "assistant", created_at: new Date().toISOString(), content: { text: `Answer to ${run.message}`, agent_id: "equity-research" } });
    run.finish();
  }
  await page.goto(`${WEB}/pod_alpha/ask`);
  await input.fill("First regression question"); await input.press("Enter");
  await page.waitForURL(/\/ask\/[0-9a-f-]{36}$/);
  const firstThread = [...threads.keys()][0], firstRun = [...runs.keys()][0];
  await fresh(() => newChat.click());
  assert.equal(cancellations, 0);
  finish(firstRun);
  await page.getByRole("link", { name: /Navigation fixture 1/ }).click();
  await page.getByText("Answer to First regression question", { exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, `/pod_alpha/ask/${firstThread}`);
  checks.push("New chat clears a promoted draft while its answer continues; saved conversation reopens");
  await fresh(() => newChat.click());
  await input.fill("Unsent text");
  await page.getByRole("button", { name: "Choose agent", exact: true }).click();
  await page.getByRole("menuitem").filter({ hasText: "Equity Research Agent" }).click();
  await fresh(() => newChat.click());
  await fresh(() => newChat.click());
  checks.push("Repeated sidebar New chat clears unsent text and explicit agent selection");
  await page.getByRole("button", { name: "Choose agent", exact: true }).focus();
  await fresh(() => page.keyboard.press("n"));
  await page.keyboard.press("Control+k");
  await page.getByPlaceholder("Search pages, chats, research, agents…").fill("page New chat");
  await fresh(() => page.getByRole("option").filter({ hasText: "New chat" }).click());
  checks.push("Keyboard N and command search create distinct empty drafts");
  await page.getByRole("link", { name: "Today", exact: true }).click();
  await page.waitForURL(/\/today$/);
  await fresh(() => page.getByRole("link", { name: /^New chat/ }).last().click());
  checks.push("Today New chat avoids cached drafts");
  await input.fill("Second regression question"); await input.press("Enter");
  await page.waitForURL(/\/ask\/[0-9a-f-]{36}$/);
  const secondRun = [...runs.keys()][1]; finish(secondRun);
  await page.getByText("Answer to Second regression question", { exact: true }).waitFor();
  assert.equal(await page.getByText("First regression question", { exact: true }).filter({ visible: true }).count(), 0);
  await fresh(() => newChat.click());
  checks.push("A new send creates a separate thread without the previous conversation");
  let release;
  slowAccept = new Promise(resolve => { release = resolve; });
  const submitting = page.waitForRequest(request => /\/threads\/[^/]+\/runs$/.test(request.url()) && request.method() === "POST");
  await input.fill("Delayed acceptance question"); await input.press("Enter");
  await submitting;
  await fresh(() => newChat.click());
  const freshURL = page.url();
  const accepted = page.waitForResponse(response => /\/threads\/[^/]+\/runs$/.test(response.url()));
  release();
  await accepted;
  finish([...runs.keys()][2]);
  await page.waitForTimeout(500);
  assert.equal(page.url(), freshURL); await empty();
  checks.push("Late acceptance of the old question cannot redirect the new draft");
  const saved = await ctx.request.get(`${API}/v1/pods/pod_alpha/threads`);
  assert.ok(saved.ok());
  const existing = (await saved.json())[0];
  if (existing) {
    await page.goto(`${WEB}/pod_alpha/ask/${existing.thread_id}`);
    await page.getByRole("button", { name: "Choose agent", exact: true }).waitFor();
    await fresh(() => newChat.click());
    checks.push("New chat also clears an actual saved conversation from the demo API");
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await input.fill("Mobile unsent draft");
  await page.getByRole("button", { name: "Toggle Sidebar", exact: true }).click();
  await fresh(() => page.getByRole("link", { name: /^New chat/ }).filter({ visible: true }).click());
  checks.push("Mobile New chat resets the composer");
  assert.equal(cancellations, 0); assert.equal(threads.size, 3); assert.deepEqual(errors, []);
  await page.screenshot({ path: `${OUT}/fresh-chat.png` });
  writeFileSync(`${OUT}/checks.json`, JSON.stringify({ checks, cancellations, createdThreads: threads.size, errors }, null, 2));
  console.log(JSON.stringify({ checks, cancellations, createdThreads: threads.size, errors }, null, 2));
} finally { for (const run of runs.values()) run.finish(); await browser.close(); }
