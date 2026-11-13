import { mountWomby } from "./womby.js";

const office = document.getElementById("office");
const toolbar = document.querySelector(".office-tools");
const launcher = document.createElement("button");
launcher.type = "button";
launcher.textContent = "Try Neo";
launcher.setAttribute("aria-haspopup", "dialog");
launcher.setAttribute("aria-controls", "neo-demo");
launcher.setAttribute("aria-expanded", "false");
if (toolbar) {
  toolbar.querySelector('a[href="/dashboard"]')?.remove();
  const address = window.location.hostname.split(".");
  const tailnetBlock = Number(address[1]);
  if (window.location.protocol === "http:" && address.length === 4 && address[0] === "100"
      && tailnetBlock >= 64 && tailnetBlock <= 127) {
    const dashboard = document.createElement("a");
    dashboard.href = "https://neo.madewithfelix.app/dashboard";
    dashboard.textContent = "Dashboard";
    toolbar.append(dashboard);
  }
  toolbar.append(launcher);
}

const dialog = document.createElement("dialog");
dialog.id = "neo-demo";
dialog.className = "neo-demo";
dialog.setAttribute("aria-labelledby", "neo-demo-title");
dialog.innerHTML = `
  <div class="neo-demo-layout">
    <header class="neo-demo-header">
      <h2 id="neo-demo-title">
        <span class="neo-demo-mark" aria-hidden="true"></span>
        <span class="neo-demo-header-copy"><span class="neo-demo-header-name">Neo-D LedgerCraft</span><span class="neo-demo-header-status">bot</span></span>
      </h2>
      <button type="button" class="neo-demo-close" aria-label="Close Neo chat" autofocus>×</button>
    </header>
    <aside class="neo-demo-companion" aria-labelledby="neo-demo-womby-name">
      <div class="neo-demo-model" aria-hidden="true"></div>
      <div class="neo-demo-companion-copy">
        <strong id="neo-demo-womby-name">Womby</strong>
        <span id="neo-demo-xp" aria-live="polite">— XP</span>
      </div>
    </aside>
    <section class="neo-demo-chat" aria-label="Chat with Neo">
      <div class="neo-demo-info">
        <p><strong>Fictional data only.</strong> Temporary demo.</p>
        <details class="neo-demo-samples">
          <summary>Sample receipts</summary>
          <div>
            <a href="/demo/samples/harbour-cafe.jpg" download>Harbour Cafe · JPG</a>
            <a href="/demo/samples/paper-gum.jpg" download>Paper Gum · JPG</a>
            <a href="/demo/samples/studio-supply.pdf" download>Studio Supply · PDF</a>
          </div>
          <p id="neo-demo-mode" hidden></p>
        </details>
      </div>
      <div id="neo-demo-log" class="neo-demo-log" role="log" aria-label="Chat messages" aria-live="polite"></div>
      <footer class="neo-demo-footer">
        <p id="neo-demo-status" class="neo-demo-status" role="status">Connecting…</p>
        <p id="neo-demo-error" class="neo-demo-error" role="alert" hidden></p>
        <form id="neo-demo-form">
          <ul id="neo-demo-files" class="neo-demo-files" aria-label="Attachments" hidden></ul>
          <p id="neo-demo-attachment-status" class="neo-demo-help" role="status" hidden></p>
          <div class="neo-demo-composer">
            <button id="neo-demo-attach" type="button" aria-label="Attach photos or a PDF" aria-describedby="neo-demo-attachment-help" title="Attach photos or a PDF">
              <svg class="neo-demo-attach-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m9 13 5-5a2.4 2.4 0 0 1 3.4 3.4l-7 7a4.2 4.2 0 0 1-6-6l8-8a5.6 5.6 0 0 1 8 8l-7 7"/></svg>
            </button>
            <input id="neo-demo-picker" type="file" multiple accept=".jpg,.jpeg,.png,.heic,.heif,.pdf,image/jpeg,image/png,image/heic,image/heif,application/pdf" hidden>
            <label class="neo-demo-sr" for="neo-demo-note">Receipt note or caption</label>
            <textarea id="neo-demo-note" rows="1" maxlength="300" placeholder="e.g. Bunnings hammer $45.90"></textarea>
            <button id="neo-demo-send" type="submit" aria-label="Send" disabled>
              <svg class="neo-demo-send-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 3 9.5 14.5M21 3l-7 18-4.5-6.5L3 10l18-7Z"/></svg>
            </button>
          </div>
          <p id="neo-demo-attachment-help" class="neo-demo-sr">Up to 10 photos or 1 PDF. PDF limit 2 MB.</p>
        </form>
      </footer>
    </section>
  </div>
`;
const workspace = document.createElement("div");
workspace.className = "neo-demo-workspace";
if (office) {
  office.before(workspace);
  workspace.append(office, dialog);
} else {
  document.body.append(dialog);
}

const byId = (name) => document.getElementById("neo-demo-" + name);
const log = byId("log");
const note = byId("note");
const picker = byId("picker");
const attach = byId("attach");
const send = byId("send");
const stages = {
  neo_intake: "Neo is receiving your upload",
  corgi_reading: "Corgi is reading the receipt",
  bear_checking: "Bear is checking the details",
  girraffe_filing: "Girraffe is updating the ledger",
};
const activeStates = new Set(["queued", "running"]);
const editableStates = new Set(["recorded", "confirmed", "corrected"]);
const streams = { abn: "ABN", payg: "PAYG", personal: "Personal" };
const messageTime = new Intl.DateTimeFormat("en-AU", { hour: "numeric", minute: "2-digit" });
const maxFileBytes = 2 * 1024 * 1024;
const maxBatchBytes = 10 * 1024 * 1024;
const maxPhotoInputBytes = 25 * 1024 * 1024;
const maxPhotoPixels = 64_000_000;
const heicFile = /\.hei[cf]$/i;
const photoSettings = [[2600, 0.88], [2200, 0.82], [1800, 0.76], [1250, 0.7]];

let files = [];
let companion;
let session;
let connecting;
let submitting = false;
let correcting = false;
let preparingAttachments = false;
let activeJob;
let intakeContext;
let pollTimer;
let replyTimer;
let seenJob;
let submittedNote;
let dateQuestion;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function addMessage(sender, content) {
  const bubble = node("article", "", "neo-demo-bubble neo-demo-bubble--" + sender);
  bubble.append(node("span", sender === "neo" ? "Neo" : "You", "neo-demo-author"));
  bubble.append(typeof content === "string" ? node("p", content) : content);
  const now = new Date();
  const time = node("time", messageTime.format(now), "neo-demo-bubble-time");
  time.dateTime = now.toISOString();
  bubble.append(time);
  log.append(bubble);
  log.scrollTop = log.scrollHeight;
}

function setError(message = "") {
  const error = byId("error");
  error.textContent = message;
  error.hidden = !message;
}

function setStatus(message) {
  const status = byId("status");
  if (status.textContent !== message) status.textContent = message;
  status.hidden = message === "Ready";
}

function updateControls() {
  const editingAttachments = submitting || preparingAttachments;
  const busy = editingAttachments || correcting || Boolean(activeJob);
  send.disabled = busy || (!note.value.trim() && !files.length);
  attach.disabled = editingAttachments;
  byId("files").querySelectorAll("button").forEach((button) => { button.disabled = editingAttachments; });
  dateQuestion?.form.querySelectorAll("button, input").forEach((control) => { control.disabled = busy; });
  log.querySelectorAll(".neo-demo-action, .neo-demo-receipt-settings :is(button, select, input)")
    .forEach((control) => { control.disabled = busy; });
  dialog.toggleAttribute("data-processing", busy);
}

function updateSession(next) {
  session = next;
  byId("xp").textContent = next.xp + " XP";
  const mode = byId("mode");
  mode.hidden = false;
  if (next.mode === "gemini") {
    mode.textContent = "Downloads use preset extraction. Other uploads use Gemini.";
  } else if (next.mode === "local") {
    mode.textContent = "Downloads use preset extraction. Other photos use local OCR.";
  } else {
    mode.textContent = "Sample files only, with preset extraction.";
  }
}

function clearDateQuestion() {
  dateQuestion?.form.remove();
  dateQuestion = null;
}

function asksForDate(result) {
  return result.status === "needs_review" && !result.receipt_id && !result.name && !result.date
    && Boolean(result.vendor) && Number.isFinite(result.total_amount) && result.total_amount > 0;
}

function createDateQuestion(text) {
  const form = node("form", "", "neo-demo-date-options");
  const today = node("button", "Today");
  today.type = "button";
  const date = node("input");
  date.type = "date";
  date.required = true;
  date.setAttribute("aria-label", "Transaction date");
  const useDate = node("button", "Use date");
  useDate.type = "submit";
  form.append(today, date, useDate);
  const question = { text, form };
  dateQuestion = question;
  today.addEventListener("click", () => confirmDate(question, "today", "Today (Sydney)"));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (date.value) confirmDate(question, date.value, date.value.split("-").reverse().join("/"));
  });
  return form;
}

function confirmDate(question, date, label) {
  if (question !== dateQuestion) return;
  const datedNote = question.text + " on " + date;
  if (datedNote.length > 300) {
    setError("Send a shorter version of the note with its date.");
    return;
  }
  sendIntake(datedNote, [], { question, label });
}

function requiredSelect(label, choices, current) {
  const field = node("label", label);
  const select = node("select");
  select.required = true;
  select.add(new Option("Choose…", ""));
  for (const [value, text] of choices) select.add(new Option(text, value));
  select.value = current ?? "";
  field.append(select);
  return { field, select };
}

function receiptSettings(container, result, stream, opener) {
  const form = node("form");
  const current = result.claim_stream === stream ? result : null;
  const activity = stream === "abn" ? requiredSelect("Activity", [
    ["dog_walking", "Dog Walking"], ["printing_3d", "3D Printing"],
  ], current?.business_activity) : null;
  const shares = [100, 80, 50, 30];
  if (current?.business_pct > 0 && !shares.includes(current.business_pct)) {
    shares.push(current.business_pct);
    shares.sort((a, b) => b - a);
  }
  const share = requiredSelect(stream === "abn" ? "Business use" : "Work use",
    shares.map((pct) => [String(pct), pct + "%"]), current?.business_pct);
  if (activity) form.append(activity.field);
  const save = node("button", "Update receipt");
  save.type = "submit";
  form.append(share.field, save);
  container.replaceChildren(form);
  (activity?.select || share.select).focus();
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const payload = { claim_stream: stream, business_pct: Number(share.select.value) };
    if (activity) payload.business_activity = activity.select.value;
    correctReceipt(container.parentElement, result, payload, opener);
  });
}

function categoryPicker(settings, result, opener, confirmCurrent) {
  const choices = node("div", "", "neo-demo-category-options");
  choices.setAttribute("role", "group");
  choices.setAttribute("aria-label", "Receipt categories");
  const close = () => {
    settings.replaceChildren();
    opener.setAttribute("aria-expanded", "false");
    opener.focus();
  };
  for (const { id, label, emoji } of result.category_options) {
    const selected = id === result.category;
    const option = node("button", "", "neo-demo-action neo-demo-category-option");
    option.type = "button";
    option.setAttribute("aria-pressed", String(selected));
    const icon = node("span", emoji);
    icon.setAttribute("aria-hidden", "true");
    option.append(icon, node("span", label));
    if (selected) {
      const check = node("span", "✓", "neo-demo-category-check");
      check.setAttribute("aria-hidden", "true");
      option.append(check);
    }
    option.addEventListener("click", () => {
      if (selected && !confirmCurrent) close();
      else correctReceipt(settings.parentElement, result, { category: id }, option);
    });
    choices.append(option);
  }
  choices.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || correcting) return;
    event.preventDefault();
    close();
  });
  settings.replaceChildren(choices);
  const selected = choices.querySelector('[aria-pressed="true"]');
  (selected || choices.firstElementChild)?.focus();
}

function renderReceipt(container, result) {
  container.replaceChildren(node("p", result.message || "Neo finished checking this receipt."));
  const status = result.receipt_status ?? result.status;
  const needsReview = status === "needs_review";
  const posted = editableStates.has(status);
  const canAllocate = (posted || needsReview) && Object.hasOwn(streams, result.claim_stream);
  const canCategorize = (posted || needsReview) && Array.isArray(result.category_options)
    && result.category_options.length > 0;
  if (!result.receipt_id || (!canAllocate && !canCategorize)) return;

  const settings = node("div", "", "neo-demo-receipt-settings");
  let categoryButton;
  if (canCategorize) {
    categoryButton = node("button", "", "neo-demo-action neo-demo-category-button");
    categoryButton.type = "button";
    categoryButton.setAttribute("aria-expanded", "false");
    const icon = node("span", "✏️");
    icon.setAttribute("aria-hidden", "true");
    categoryButton.append(icon, node("span", "Category"));
    categoryButton.addEventListener("click", () => {
      const open = categoryButton.getAttribute("aria-expanded") === "false";
      categoryButton.setAttribute("aria-expanded", String(open));
      if (open) categoryPicker(settings, result, categoryButton, needsReview);
      else settings.replaceChildren();
    });
    container.append(categoryButton);
  }
  if (!canAllocate) {
    container.append(settings);
    return;
  }
  if (needsReview) {
    const vendor = result.vendor ? result.vendor.replace(/^\[FICTIONAL DEMO\]\s*/i, "") : "This receipt";
    container.append(node("p", vendor + ": work or personal?", "neo-demo-question"));
  }
  const actions = node("div", "", "neo-demo-receipt-actions");
  actions.setAttribute("role", "group");
  actions.setAttribute("aria-label", "Allocation for receipt " + result.receipt_id);
  for (const [stream, label] of Object.entries(streams)) {
    const selected = !needsReview && result.claim_stream === stream;
    const button = node("button", (selected ? "✓ " : "") + label, "neo-demo-action");
    button.type = "button";
    button.setAttribute("aria-label", label);
    button.setAttribute("aria-pressed", String(selected));
    button.addEventListener("click", () => {
      categoryButton?.setAttribute("aria-expanded", "false");
      if (stream === "personal") {
        settings.replaceChildren();
        if (!selected) correctReceipt(container, result, { claim_stream: stream }, button);
      } else {
        receiptSettings(settings, result, stream, button);
      }
    });
    actions.append(button);
  }
  container.append(actions, settings);
}

async function correctReceipt(container, result, allocation, opener) {
  if (submitting || correcting || preparingAttachments || activeJob) return;
  if (!session) {
    setError("Your demo session expired. Send a new receipt to start again.");
    return;
  }
  correcting = true;
  setError();
  setStatus("Updating receipt…");
  updateControls();
  let focus = opener;
  try {
    const snapshot = await api("correct", "POST", { receipt_id: result.receipt_id, ...allocation });
    updateSession(snapshot.session);
    renderReceipt(container, snapshot.result);
    focus = container.querySelector(allocation.category ? ".neo-demo-category-button" : '.neo-demo-receipt-actions [aria-pressed="true"]');
  } catch (error) {
    if (error.status === 401) session = null;
    setError(error.status === 401 ? "Your demo session expired. Send a new receipt to start again." : error.message);
  } finally {
    correcting = false;
    setStatus(session ? "Ready" : "Session expired");
    updateControls();
    if (dialog.open) focus?.focus({ preventScroll: true });
  }
}

function showResults(job) {
  const results = job.results || [];
  const list = node("ul", "", "neo-demo-results");
  for (const result of results) {
    const line = node("li");
    if (results.length > 1 && result.name) line.append(node("strong", result.name));
    if (result.status === "photo_confirmation") {
      line.append(node("p", result.message), photoConfirmationForm(result));
    } else if (asksForDate(result)) {
      const originalNote = submittedNote?.id === job.id ? submittedNote.text : null;
      line.append(node("p", originalNote ? "What date was this purchase?"
        : "Please send the note with one transaction date, for example Coffee 5.5 on today."));
      if (originalNote) line.append(createDateQuestion(originalNote));
    } else {
      const receipt = node("div", "", "neo-demo-receipt");
      renderReceipt(receipt, result);
      line.append(receipt);
    }
    list.append(line);
  }
  if (list.children.length) addMessage("neo", list);
}

function photoConfirmationForm(result) {
  const container = node("div", "", "neo-demo-receipt-settings neo-demo-photo-confirmation");
  const form = node("form");
  const fields = {};
  for (const [name, label, type, value] of [
    ["vendor", "Vendor", "text", result.vendor],
    ["date", "Transaction date", "date", result.date],
    ["total_amount", "Total (AUD)", "number", result.total_amount],
  ]) {
    const field = node("label", label);
    const input = node("input");
    input.type = type;
    input.required = true;
    input.value = value ?? "";
    fields[name] = input;
    field.append(input);
    form.append(field);
  }
  fields.vendor.maxLength = 100;
  fields.total_amount.inputMode = "decimal";
  fields.total_amount.min = "0.01";
  fields.total_amount.max = "9999999.99";
  fields.total_amount.step = "0.01";
  const record = node("button", "Record receipt");
  record.type = "submit";
  form.append(record);
  container.append(form);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const vendor = fields.vendor.value.trim();
    if (!vendor) {
      fields.vendor.focus();
      setError("Enter the vendor from the receipt.");
      return;
    }
    sendIntake("", [], {
      form: container,
      label: "Recorded receipt details",
      photo: { draft_id: result.draft_id, vendor, date: fields.date.value, total_amount: fields.total_amount.valueAsNumber },
    });
  });
  return container;
}

async function api(path, method = "GET", payload) {
  const options = { method, credentials: "same-origin", cache: "no-store" };
  if (payload) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(payload);
  }
  const response = await fetch("/demo/api/" + path, options);
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(result.error || "Neo couldn’t connect. Try again shortly.");
    error.status = response.status;
    throw error;
  }
  return result;
}

function updateJob(job, animate = true) {
  clearTimeout(pollTimer);
  const context = job && intakeContext?.jobId === job.id ? intakeContext : null;
  if (context && job.run_ids?.length) {
    office?.dispatchEvent(new CustomEvent("neo-office:receipt-context", {
      detail: { runIds: job.run_ids, kind: context.kind, note: context.note },
    }));
  }
  if (job) office?.dispatchEvent(new Event("neo-office:refresh-progress"));
  if (!job) {
    activeJob = null;
    updateControls();
    setStatus(session ? "Ready" : "Connecting…");
    return;
  }
  if (activeStates.has(job.state)) {
    activeJob = job.id;
    const progress = job.total > 1 ? " · " + job.completed + " of " + job.total + " checked" : "";
    const received = context?.note ? `Neo received “${context.note}”` : "Neo received your receipt";
    const stage = stages[job.stage] || "Neo is preparing your request";
    if (job.state === "queued") {
      setStatus("Queued · " + received);
    } else if (job.stage === "terminal") {
      setStatus("Finishing receipt processing…" + progress);
    } else {
      setStatus("Live · " + (job.stage === "neo_intake" ? received : stage) + progress);
    }
    companion?.setState("thinking");
    updateControls();
    pollTimer = setTimeout(pollJob, 750);
    return;
  }

  activeJob = null;
  setStatus("Ready");
  if (job.id !== seenJob) {
    seenJob = job.id;
    showResults(job);
    if (job.state === "failed" && !job.results?.length) {
      setError(job.error || "Neo couldn’t finish this request. Try again with a clearer receipt.");
    }
    clearTimeout(replyTimer);
    companion?.setState(animate ? "answering" : "idle");
    if (animate) {
      if (job.xp?.gained > 0) companion?.celebrate(job.xp);
      replyTimer = setTimeout(() => {
        if (!activeJob && !submitting) companion?.setState(note.value ? "listening" : "idle");
      }, 3200);
    }
  }
  updateControls();
}

async function pollJob() {
  try {
    const snapshot = await api("session");
    updateSession(snapshot.session);
    updateJob(snapshot.job);
  } catch (error) {
    if (error.status === 401) {
      session = null;
      activeJob = null;
      intakeContext = null;
      submittedNote = null;
      clearDateQuestion();
      companion?.setState("idle");
      setStatus("Session expired");
      setError("Your demo session expired. Send again to start a fresh session.");
      updateControls();
    } else {
      setStatus("Reconnecting to Neo…");
      pollTimer = setTimeout(pollJob, 2000);
    }
  }
}

async function connect() {
  if (session) return;
  if (connecting) return connecting;
  setStatus("Connecting…");
  connecting = (async () => {
    let snapshot;
    try {
      snapshot = await api("session");
    } catch (error) {
      if (error.status !== 401) throw error;
      snapshot = await api("session", "POST");
      seenJob = null;
      intakeContext = null;
      submittedNote = null;
      clearDateQuestion();
      log.replaceChildren();
      office?.dispatchEvent(new Event("neo-office:refresh-progress"));
    }
    updateSession(snapshot.session);
    updateJob(snapshot.job, false);
  })();
  try {
    await connecting;
  } finally {
    connecting = null;
  }
}

function openChat() {
  if (!dialog.open) {
    document.body.setAttribute("data-neo-demo-open", "");
    launcher.setAttribute("aria-expanded", "true");
    dialog.show();
  }
  fitPhoneLayout();
  fitKeyboard();
  if (!companion) {
    try {
      companion = mountWomby(dialog.querySelector(".neo-demo-model"));
      companion?.setState(activeJob ? "thinking" : "idle");
    } catch {
      dialog.querySelector(".neo-demo-model").hidden = true;
    }
  }
  connect().catch((error) => {
    setStatus("Disconnected");
    setError(error.message);
  });
}

function renderFiles() {
  const list = byId("files");
  list.hidden = !files.length;
  list.replaceChildren(...files.map((file, index) => {
    const item = node("li");
    item.append(node("span", file.name));
    const remove = node("button", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", "Remove " + file.name);
    remove.addEventListener("click", () => {
      files.splice(index, 1);
      setError();
      setAttachmentStatus();
      renderFiles();
      attach.focus();
    });
    item.append(remove);
    return item;
  }));
  updateControls();
}

function setAttachmentStatus(message = "") {
  const status = byId("attachment-status");
  status.textContent = message;
  status.hidden = !message;
}

async function preparePhoto(file, limit) {
  const image = new Image();
  image.decoding = "async";
  const url = URL.createObjectURL(file);
  const canvas = document.createElement("canvas");
  try {
    await new Promise((resolve, reject) => {
      image.addEventListener("load", resolve, { once: true });
      image.addEventListener("error", reject, { once: true });
      image.src = url;
    }).catch(() => {
      const suggestion = heicFile.test(file.name) ? "Export it as JPG, or upload a screenshot." : "Try another photo or upload a screenshot.";
      throw new Error("This browser couldn’t open " + file.name + ". " + suggestion);
    });
    const { naturalWidth: width, naturalHeight: height } = image;
    if (width * height > maxPhotoPixels) {
      throw new Error(file.name + " is too large to process here. Use a smaller photo or a screenshot.");
    }
    const context = canvas.getContext("2d", { alpha: false });
    const failure = "Couldn’t prepare " + file.name + ". Use a smaller JPG or a screenshot.";
    if (!context) throw new Error(failure);
    try {
      for (const [edge, quality] of photoSettings) {
        const scale = Math.min(1, edge / Math.max(width, height));
        canvas.width = Math.max(1, Math.round(width * scale));
        canvas.height = Math.max(1, Math.round(height * scale));
        context.fillStyle = "#fff";
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        const jpg = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
        if (jpg?.type === "image/jpeg" && jpg.size && jpg.size <= limit) {
          return new File([jpg], file.name.replace(/\.[^.]+$/, ".jpg"), { type: "image/jpeg" });
        }
      }
    } catch {
      throw new Error(failure);
    }
    throw new Error(failure);
  } finally {
    image.removeAttribute("src");
    URL.revokeObjectURL(url);
    canvas.width = canvas.height = 0;
  }
}

async function acceptFiles(incoming) {
  const chosen = [...files, ...incoming];
  if (chosen.length > 10) throw new Error("Choose up to 10 photos.");
  if (chosen.some((file) => !/\.(jpe?g|png|hei[cf]|pdf)$/i.test(file.name))) {
    throw new Error("Choose JPG, PNG, or HEIC photos, or a PDF.");
  }
  if (chosen.some((file) => file.size === 0)) throw new Error("Choose non-empty photos or a PDF.");
  const pdf = chosen.find((file) => /\.pdf$/i.test(file.name));
  if (pdf && chosen.length !== 1) throw new Error("Send one PDF at a time, separately from photos.");
  if (pdf && pdf.size > maxFileBytes) throw new Error("Choose a PDF up to 2 MB, or upload photos of the pages.");
  if (chosen.some((file) => file.size > maxPhotoInputBytes)) throw new Error("Choose camera photos up to 25 MB each.");

  // Keep original bytes where possible, including the downloadable OCR fixtures.
  const projected = chosen.reduce((sum, file) => sum + (heicFile.test(file.name) ? maxFileBytes : Math.min(file.size, maxFileBytes)), 0);
  const limit = projected <= maxBatchBytes ? maxFileBytes : Math.floor(maxBatchBytes / chosen.length);
  const prepared = [];
  let resized = 0;
  for (const [index, file] of chosen.entries()) {
    if (heicFile.test(file.name) || file.size > limit) {
      setAttachmentStatus("Preparing photo " + (index + 1) + " of " + chosen.length + "…");
      prepared.push(await preparePhoto(file, limit));
      resized++;
    } else {
      prepared.push(file);
    }
  }
  files = prepared;
  renderFiles();
  setAttachmentStatus(resized ? (resized === 1 ? "1 photo prepared for upload." : resized + " photos prepared for upload.") : "");
}

function encode(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve({ name: file.name, data: String(reader.result).split(",", 2)[1] }));
    reader.addEventListener("error", () => reject(new Error("Couldn’t read " + file.name + ". Select it again.")));
    reader.readAsDataURL(file);
  });
}

function fitKeyboard() {
  if (!dialog.open) return;
  const viewport = window.visualViewport;
  const keyboard = Boolean(viewport && window.innerHeight - viewport.height > 120);
  dialog.toggleAttribute("data-keyboard", keyboard);
  document.body.style.setProperty("--neo-demo-viewport", (viewport?.height ?? window.innerHeight) + "px");
  document.body.style.setProperty("--neo-demo-top", (viewport?.offsetTop ?? 0) + "px");
}

function fitPhoneLayout() {
  const shortSide = Math.min(screen.width, screen.height);
  const phone = navigator.maxTouchPoints > 0 && shortSide > 0 && shortSide <= 600;
  dialog.toggleAttribute("data-phone", phone);
  if (!phone) return;
  const landscape = screen.orientation?.type?.startsWith("landscape") || Math.abs(window.orientation) === 90;
  const screenWidth = landscape ? Math.max(screen.width, screen.height) : shortSide;
  const scale = Math.max(1, Math.min(3, document.documentElement.clientWidth / screenWidth));
  dialog.style.setProperty("--neo-demo-scale", scale);
}

function outgoingMessage(text, chosen) {
  if (!chosen.length) return text;

  const content = node("div");
  const photos = chosen.filter((file) => /\.(jpe?g|png)$/i.test(file.name));
  if (photos.length) {
    const gallery = node("div", "", "neo-demo-attachments");
    for (const photo of photos.slice(0, 4)) {
      const preview = node("img");
      const url = URL.createObjectURL(photo);
      preview.alt = photo.name;
      preview.addEventListener("load", () => URL.revokeObjectURL(url), { once: true });
      preview.addEventListener("error", () => {
        URL.revokeObjectURL(url);
        preview.replaceWith(node("span", photo.name, "neo-demo-attachment-label"));
      }, { once: true });
      preview.src = url;
      gallery.append(preview);
    }
    content.append(gallery);
  }
  if (chosen.length > 1 || !photos.length) {
    content.append(node("span", photos.length ? photos.length + " photos" : chosen[0].name, "neo-demo-attachment-label"));
  }
  if (text) content.append(node("p", text));
  return content;
}

async function sendIntake(text, chosen, confirmation = null) {
  if (submitting || correcting || preparingAttachments || activeJob) return;
  if (!text && !chosen.length && !confirmation?.photo) return;
  submitting = true;
  note.readOnly = true;
  setError();
  updateControls();

  try {
    await connect();
    if (activeJob) return;
    if (confirmation?.question && confirmation.question !== dateQuestion) {
      setError("Send the note again to confirm its date in this session.");
      return;
    }
    if (confirmation?.form && !confirmation.form.isConnected) {
      setError("Upload the photo again to confirm its details in this session.");
      return;
    }
    setStatus(chosen.length ? "Uploading to Neo…" : "Sending to Neo…");
    companion?.setState("thinking");
    const encoded = await Promise.all(chosen.map(encode));
    const payload = confirmation?.photo ? { photo_confirmation: confirmation.photo } : { text, files: encoded };
    const snapshot = await api("intake", "POST", payload);
    clearDateQuestion();
    let kind = "note";
    if (chosen.some((file) => /\.pdf$/i.test(file.name))) kind = "pdf";
    else if (chosen.length || confirmation?.photo) kind = "photo";
    intakeContext = {
      jobId: snapshot.job.id,
      kind,
      note: kind === "note" ? text.replace(/\s+/g, " ").trim().slice(0, 96) : "",
    };
    submittedNote = chosen.length || confirmation ? null : { id: snapshot.job.id, text };
    if (confirmation) {
      confirmation.form?.remove();
      addMessage("you", confirmation.label);
    } else {
      addMessage("you", outgoingMessage(text, chosen));
      note.value = "";
      files = [];
      setAttachmentStatus();
      renderFiles();
    }
    updateSession(snapshot.session);
    updateJob(snapshot.job);
  } catch (error) {
    if (error.status === 401) {
      session = null;
      intakeContext = null;
      submittedNote = null;
      clearDateQuestion();
      setError("Your demo session expired. Send again to start a fresh session.");
    } else {
      setError(error.message);
    }
    setStatus(session ? "Ready" : "Disconnected");
    companion?.setState("idle");
  } finally {
    submitting = false;
    note.readOnly = false;
    updateControls();
  }
}

launcher.addEventListener("click", openChat);
office?.addEventListener("neo-office:agent-select", (event) => {
  if (event.detail?.id !== "chief_of_staff") return;
  event.preventDefault();
  openChat();
});
dialog.querySelector(".neo-demo-close").addEventListener("click", () => dialog.close());
dialog.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !event.defaultPrevented) dialog.close();
});
dialog.addEventListener("close", () => {
  document.body.removeAttribute("data-neo-demo-open");
  launcher.setAttribute("aria-expanded", "false");
  clearTimeout(replyTimer);
  companion?.dispose();
  companion = null;
});
attach.addEventListener("click", () => picker.click());
picker.addEventListener("change", async () => {
  const incoming = [...picker.files];
  picker.value = "";
  if (!incoming.length || submitting || preparingAttachments) return;
  preparingAttachments = true;
  setError();
  setAttachmentStatus();
  updateControls();
  try {
    await acceptFiles(incoming);
  } catch (error) {
    setAttachmentStatus();
    setError(error.message);
  } finally {
    preparingAttachments = false;
    updateControls();
  }
});
note.addEventListener("input", () => {
  updateControls();
  if (!activeJob && !submitting) companion?.setState(note.value ? "listening" : "idle");
});
note.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    byId("form").requestSubmit();
  }
});
byId("form").addEventListener("submit", (event) => {
  event.preventDefault();
  sendIntake(note.value.trim(), [...files]);
});
window.visualViewport?.addEventListener("resize", fitKeyboard);
window.visualViewport?.addEventListener("scroll", fitKeyboard);
window.addEventListener("resize", fitPhoneLayout);
window.addEventListener("resize", fitKeyboard);
fitPhoneLayout();
if (window.location.protocol === "https:" && window.location.pathname === "/demo") openChat();
