"use strict";

const byId = (id) => document.getElementById(id);
const dashboard = byId("dashboard");
const tabs = document.querySelector(".tabs");
const fiscalYear = byId("financial-year");
const spendingMonth = byId("spending-month");
const chartColors = ["#347d6b", "#5074a9", "#a26d37", "#9274a9", "#2d9da7", "#81938c"];
const moneyFormatter = new Intl.NumberFormat("en-AU", { style: "currency", currency: "AUD" });
const monthFormatter = new Intl.DateTimeFormat("en-AU", { month: "short", year: "numeric", timeZone: "UTC" });
const streamNames = { abn: "ABN", payg: "PAYG", personal: "Personal" };
let activityNames = { dog_walking: "Dog walking", printing_3d: "3D printing", payg_work: "PAYG work" };
const pageSize = 40;

let data = null;
let filter = "all";
let spendScope = "all";
let selectedTab = "receipts";
let visibleReceipts = pageSize;
let requestId = 0;

function element(tag, value = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = value;
  if (className) node.className = className;
  return node;
}

function money(amount) {
  return amount == null || !Number.isFinite(Number(amount)) ? "—" : moneyFormatter.format(amount);
}

function dateLabel(isoDate) {
  return /^\d{4}-\d{2}-\d{2}$/.test(isoDate || "") ? isoDate.split("-").reverse().join("/") : "Date unavailable";
}

function categoryLabel(category) {
  if (!category) return "Uncategorised";
  const labels = { materials_3d_printing: "3D printing materials", travel_parking: "Parking", dining_coffee: "Dining & coffee" };
  return labels[category] || (data && data.category_names && data.category_names[category]) || category.replace(/_/g, " ").replace(/^[a-z]/, (first) => first.toUpperCase());
}

function driveUrl(value) {
  try {
    const url = new URL(value);
    if (url.origin !== "https://drive.google.com") return "";
    const validOpen = url.pathname === "/open" && /^[-\w]+$/.test(url.searchParams.get("id") || "");
    return validOpen || /^\/file\/d\/[-\w]+\/view\/?$/.test(url.pathname) ? url.href : "";
  } catch {
    return "";
  }
}

function row(label, value, note = "", color = "") {
  const item = element("li");
  const description = element("span", label);
  if (color) {
    const dot = element("span", "", "legend-dot");
    dot.style.backgroundColor = color;
    description.prepend(dot);
  }
  if (note) description.append(element("small", note, "row-note"));
  item.append(description, element("span", value, "money"));
  return item;
}

function fillList(id, rows, emptyMessage) {
  byId(id).replaceChildren(...(rows.length ? rows : [element("li", emptyMessage, "muted")]));
}

function fillSelect(select, values, current, format) {
  const options = values.map((value) => new Option(format(value), value));
  select.replaceChildren(...options);
  select.value = current;
}

function fyLabel(year) {
  return "FY" + year + "–" + String(Number(year) + 1).slice(-2);
}

function filtersFor(selector, value) {
  document.querySelectorAll(selector).forEach((button) => {
    const key = button.dataset.filter || button.dataset.scope;
    button.setAttribute("aria-pressed", String(key === value));
  });
}

function showTab(tab, scroll = true) {
  selectedTab = tab;
  tabs.querySelectorAll("button").forEach((button) => {
    const current = button.dataset.tab === tab;
    byId(button.dataset.tab).hidden = !current;
    if (current) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
  });
  if (scroll) window.scrollTo(0, 0);
}

function renderReceipts() {
  const query = byId("receipt-search").value.trim().toLowerCase();
  const receipts = data.receipts.filter((receipt) => {
    const match = filter === "all" || (filter === "check" && receipt.check)
      || (filter === "duplicate" && receipt.duplicate) || receipt.claim_stream === filter;
    if (!match || !query) return match;
    const items = (receipt.items || []).map((item) => item.description).join(" ");
    return [receipt.vendor, categoryLabel(receipt.category), items, "#" + receipt.id]
      .join(" ").toLowerCase().includes(query);
  });

  const empty = byId("receipt-empty");
  empty.hidden = receipts.length > 0;
  empty.textContent = query || filter !== "all" ? "No matching receipts." : "No receipts in this financial year.";
  byId("more-receipts").hidden = receipts.length <= visibleReceipts;
  const list = byId("receipt-list");
  list.replaceChildren(...receipts.slice(0, visibleReceipts).map((receipt) => {
    const line = element("li");
    const button = element("button", "", "receipt-row");
    button.type = "button";
    button.dataset.receipt = receipt.id;
    button.setAttribute("aria-haspopup", "dialog");
    const metadata = ["#" + receipt.id, dateLabel(receipt.date), streamNames[receipt.claim_stream], categoryLabel(receipt.category)];
    button.append(
      element("strong", receipt.vendor, "vendor"),
      element("span", money(receipt.amount), "money"),
      element("span", metadata.filter(Boolean).join(" · "), "receipt-meta"),
      element("span", receipt.claim > 0 && !receipt.check ? "Claim " + money(receipt.claim) : "", "receipt-claim")
    );
    if (receipt.duplicate) button.append(element("span", "Duplicate", "badge"));
    else if (receipt.check) button.append(element("span", "Needs review", "badge review"));
    line.append(button);
    return line;
  }));
}

function showReceipt(receipt) {
  byId("receipt-title").textContent = receipt.vendor;
  const explicitGst = receipt.raw_tax_amount ?? (receipt.gst_status === "explicit" ? receipt.tax_amount : null);
  const fields = [
    ["Receipt", "#" + receipt.id],
    ["Date", dateLabel(receipt.date)],
    ["Total", money(receipt.amount) + " AUD"],
    ["GST", money(explicitGst)],
    ["Stream", streamNames[receipt.claim_stream]],
    ["Activity", activityNames[receipt.business_activity]],
    ["Category", categoryLabel(receipt.category)],
    ["Payment", receipt.payment_method],
    ["Suggested myTax", receipt.tax_label],
    ["Status", receipt.duplicate ? "Duplicate · excluded from totals" : receipt.check ? "Needs review" : "Recorded"],
    ["Review note", receipt.flag_reason],
  ];
  if (!receipt.duplicate && !receipt.check && receipt.claim > 0) fields.push(["Estimated claim", money(receipt.claim)]);
  byId("receipt-details").replaceChildren(...fields.filter(([, value]) => value).flatMap(([label, value]) => [
    element("dt", label), element("dd", value),
  ]));
  byId("receipt-items").replaceChildren(...(receipt.items || []).map((item) => row(item.description, money(item.amount))));
  const original = byId("receipt-original");
  const url = driveUrl(receipt.gdrive_url);
  original.hidden = !url;
  if (url) original.href = url;
  else original.removeAttribute("href");
  byId("receipt-dialog").showModal();
}

function renderSpendingChart() {
  const group = data.mytax.where_money_goes[spendScope];
  const total = group.total;
  const categories = group.items.slice(0, 5);
  const remainder = group.items.slice(5);
  if (remainder.length) {
    const amount = remainder.reduce((sum, item) => sum + item.amount, 0);
    categories.push({ category: "Other (" + remainder.length + ")", amount });
  }
  let position = 0;
  const stops = [];
  const rows = categories.map((item, index) => {
    const percent = total > 0 ? 100 * item.amount / total : 0;
    const color = chartColors[index];
    stops.push(color + " " + position.toFixed(4) + "% " + Math.min(100, position + percent).toFixed(4) + "%");
    position += percent;
    return row(categoryLabel(item.category), money(item.amount), Math.round(percent) + "% of spending", color);
  });
  byId("spend-donut").style.backgroundImage = stops.length > 1 ? "conic-gradient(" + stops.join(",") + ")" : "";
  byId("spend-donut").style.backgroundColor = stops.length === 1 ? chartColors[0] : "";
  byId("spend-total").textContent = money(total);
  fillList("spend-categories", rows, "No recorded spending for this selection.");
}

function renderMyTax() {
  const tax = data.mytax;
  byId("tax-title").textContent = "Estimated claimable expenses · " + fyLabel(data.fy_start_year);
  byId("tax-total").textContent = money(tax.total_claimable);
  byId("tax-context").textContent = tax.receipt_count + " receipts counted · Personal " + money(tax.personal_total);
  const review = byId("tax-review");
  review.hidden = tax.needs_review_count === 0;
  review.textContent = tax.needs_review_count + " awaiting review · " + money(tax.pending_review_claimable) + " in potential claims excluded";
  const breakdown = Object.entries(tax.abn_by_business).map(([activity, amount]) => row(activityNames[activity], money(amount)));
  if (tax.payg_total > 0) breakdown.push(row("PAYG", money(tax.payg_total)));
  fillList("tax-business", breakdown, "No ABN expenses.");
  renderSpendingChart();
  fillList("tax-labels", tax.by_mytax_label.map((entry) => row(entry.tax_label || entry.label, money(entry.gross_amount))), "No recorded myTax categories.");
  fillList("tax-categories", tax.by_category.map((entry) => row(categoryLabel(entry.category), money(entry.amount))), "No recorded claimable expenses.");
  byId("export-link").href = "/api/export.csv?fy=" + encodeURIComponent(data.fy_start_year);
  byId("tax-disclaimer").textContent = "Review items and duplicates are excluded from totals. Cash basis: income counts when received; unpaid invoices and bills are not tracked. " + data.suggestion_disclaimer;
}

function renderBooks() {
  const books = data.books;
  byId("books-title").textContent = books.net_profit < 0 ? "Business loss" : "Business result";
  byId("books-profit").textContent = money(books.net_profit);
  byId("books-profit").classList.toggle("negative", books.net_profit < 0);
  byId("books-period").textContent = dateLabel(data.period.start) + " – " + dateLabel(data.period.end);
  const spending = books.spending;
  fillSelect(spendingMonth, books.available_months, spending.month, (month) => monthFormatter.format(new Date(month + "-01T00:00:00Z")));
  byId("books-spending").textContent = money(spending.total_spent) + " spent";
  byId("books-spending-context").textContent = "Personal " + money(spending.personal_share) + " · Business " + money(spending.business_claimable);
  const bars = spending.by_category.map((entry) => {
    const item = element("li", "", "bar");
    const heading = element("div", "", "bar-heading");
    const category = categoryLabel(entry.category);
    heading.append(element("span", category), element("span", money(entry.amount), "money"));
    const progress = element("progress");
    progress.max = 100;
    progress.value = Math.max(0, Math.min(100, entry.pct));
    progress.setAttribute("aria-label", category + " share of spending");
    item.append(heading, progress);
    return item;
  });
  fillList("books-categories", bars, "No recorded spending this month.");
  fillList("books-activity", (books.businesses || []).map((result) => row(result.icon + " " + result.name, money(result.net), "Income " + money(result.income) + " · Expenses " + money(result.expenses))));
  const accounts = [...books.bank_accounts, ...books.liabilities];
  byId("books-net-worth").textContent = accounts.length ? "Net position in ledger: " + money(books.net_worth) : "No balances recorded.";
  fillList("books-accounts", accounts.map((account) => row(account.name, money(account.net_balance))), "");
  byId("books-accounts").hidden = !accounts.length;
}

function gate(title, message, signIn = false, retry = false) {
  byId("gate-title").textContent = title;
  byId("gate-message").textContent = message;
  byId("gate").hidden = false;
  byId("telegram-sign-in").hidden = !signIn;
  byId("sign-in-code").hidden = !signIn;
  byId("retry").hidden = !retry;
}

function requireSignIn(message = "Use /dashboard in Neo’s Telegram chat to get a one-time link or code.") {
  if (data) {
    location.replace("/dashboard");
    return;
  }
  gate("Open your ledger", message, true);
}

function requestOptions(method, payload) {
  const options = { method, credentials: "same-origin", cache: "no-store" };
  if (payload) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(payload);
  }
  return options;
}

async function loadDashboard(year = fiscalYear.value, month = spendingMonth.value) {
  const id = ++requestId;
  const query = new URLSearchParams();
  if (year) query.set("fy", year);
  if (month) query.set("month", month);
  const notice = byId("connection-status");
  byId("refresh").disabled = true;

  try {
    const response = await fetch("/api/dashboard?" + query, requestOptions("GET"));
    if (id !== requestId) return;
    if (response.status === 401) {
      requireSignIn();
      return;
    }
    if (!response.ok) throw new Error("Dashboard unavailable");
    const result = await response.json();
    if (id !== requestId) return;
    const firstLoad = !data;
    const changed = firstLoad || JSON.stringify(result) !== JSON.stringify(data);
    if (changed) {
      data = result;
      if (result.activity_names) activityNames = result.activity_names;
      fillSelect(fiscalYear, data.available_fys, data.fy_start_year, fyLabel);
      renderReceipts();
      renderMyTax();
      renderBooks();
    }
    if (firstLoad) showTab(selectedTab, false);
    [dashboard, tabs, fiscalYear, byId("refresh")].forEach((node) => { node.hidden = false; });
    byId("gate").hidden = true;
    notice.hidden = true;
  } catch {
    if (id !== requestId) return;
    if (data) {
      fiscalYear.value = data.fy_start_year;
      spendingMonth.value = data.books.spending.month;
      notice.textContent = "Couldn’t refresh. Showing the last loaded figures.";
      notice.hidden = false;
    } else {
      gate("Dashboard unavailable", "Neo couldn’t connect to the ledger.", false, true);
    }
  } finally {
    if (id === requestId) byId("refresh").disabled = false;
  }
}

async function exchangeLoginCode(code) {
  gate("Signing in…", "");
  try {
    const response = await fetch("/api/dashboard/session", requestOptions("POST", { code }));
    if (response.ok) await loadDashboard();
    else if (response.status === 401) requireSignIn("This sign-in code expired or was already used. Request a fresh one in Telegram.");
    else throw new Error("Sign-in unavailable");
  } catch {
    gate("Sign-in unavailable", "Request a fresh sign-in code in Telegram and try again.", true);
  }
}

async function begin() {
  const code = new URLSearchParams(location.hash.slice(1)).get("code");
  if (!code) {
    await loadDashboard();
    return;
  }
  history.replaceState(null, "", location.pathname + location.search);
  await exchangeLoginCode(code);
}

function showFormStatus(id, message) {
  byId(id).textContent = message;
  byId(id).hidden = !message;
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result).split(",", 2)[1]));
    reader.addEventListener("error", reject);
    reader.readAsDataURL(file);
  });
}

async function sendForm(form, path, statusId, buildPayload, successMessage) {
  const submit = form.querySelector("[type=submit]");
  if (submit.disabled) return false;
  submit.disabled = true;
  try {
    const payload = await buildPayload();
    if (!payload) return false;
    showFormStatus(statusId, "Sending…");
    const response = await fetch(path, requestOptions("POST", payload));
    if (response.status === 401) {
      requireSignIn();
      return false;
    }
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      showFormStatus(statusId, (result.summary_text || result.error || "Couldn’t complete the request.").replace(/[*`]/g, ""));
      return false;
    }
    showFormStatus(statusId, successMessage);
    await loadDashboard();
    return true;
  } catch {
    showFormStatus(statusId, "Connection interrupted. Refresh the ledger before trying again.");
    return false;
  } finally {
    submit.disabled = false;
  }
}

tabs.addEventListener("click", (event) => {
  const button = event.target.closest("[data-tab]");
  if (button) showTab(button.dataset.tab);
});
document.querySelector("#receipts .filters").addEventListener("click", (event) => {
  const button = event.target.closest("[data-filter]");
  if (!button) return;
  filter = button.dataset.filter;
  visibleReceipts = pageSize;
  filtersFor("[data-filter]", filter);
  renderReceipts();
});
document.querySelector(".spend-filters").addEventListener("click", (event) => {
  const button = event.target.closest("[data-scope]");
  if (!button) return;
  spendScope = button.dataset.scope;
  filtersFor("[data-scope]", spendScope);
  renderSpendingChart();
});
byId("receipt-search").addEventListener("input", () => {
  visibleReceipts = pageSize;
  renderReceipts();
});
byId("more-receipts").addEventListener("click", () => {
  const next = visibleReceipts;
  visibleReceipts += pageSize;
  renderReceipts();
  byId("receipt-list").children[next]?.querySelector("button")?.focus();
});
byId("receipt-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-receipt]");
  if (!button) return;
  const receipt = data.receipts.find((item) => String(item.id) === button.dataset.receipt);
  if (receipt) showReceipt(receipt);
});
fiscalYear.addEventListener("change", () => {
  visibleReceipts = pageSize;
  loadDashboard(fiscalYear.value, "");
});
spendingMonth.addEventListener("change", () => loadDashboard());
byId("refresh").addEventListener("click", () => loadDashboard());
byId("retry").addEventListener("click", () => loadDashboard());

byId("sign-in-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = byId("login-code");
  const code = input.value.trim();
  if (!/^[A-Za-z0-9_-]{43}$/.test(code)) {
    showFormStatus("code-status", "Paste the sign-in code copied from Telegram.");
    return;
  }
  input.value = "";
  showFormStatus("code-status", "");
  await exchangeLoginCode(code);
});

byId("intake-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const saved = await sendForm(form, "/api/dashboard/intake", "intake-status", async () => {
    const text = byId("intake-note").value.trim();
    const file = byId("intake-file").files[0];
    if (!text && !file) {
      showFormStatus("intake-status", "Add a note, photo or PDF.");
      return null;
    }
    if (file && file.size > 5 * 1024 * 1024) {
      showFormStatus("intake-status", "Choose a file under 5 MB.");
      return null;
    }
    const payload = { text };
    if (file) {
      payload.file_data = await readFile(file);
      payload.filename = file.name;
    }
    return payload;
  }, "Receipt processed. Check the Receipts tab for its status.");
  if (saved) form.reset();
});

byId("open-companion").addEventListener("click", () => {
  const state = data.companion;
  byId("companion-summary").textContent = state.total_xp + " lifetime XP · " + state.boxes_unlocked + " rewards unlocked";
  byId("reward-name").value = state.reward_name;
  byId("reward-target").value = state.reward_target_xp;
  byId("reward-status").hidden = true;
  byId("companion-dialog").showModal();
});
byId("reward-form").addEventListener("submit", (event) => {
  event.preventDefault();
  sendForm(event.currentTarget, "/api/dashboard/reward", "reward-status", () => ({
    reward_name: byId("reward-name").value.trim(),
    reward_target_xp: byId("reward-target").valueAsNumber,
  }), "Reward saved.");
});
byId("sign-out").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/dashboard/session", requestOptions("DELETE"));
    if (!response.ok) throw new Error("Couldn’t sign out");
    location.replace("/dashboard");
  } catch {
    const notice = byId("connection-status");
    notice.textContent = "Couldn’t sign out. Please try again.";
    notice.hidden = false;
    notice.scrollIntoView({ block: "nearest" });
  }
});

window.setInterval(() => {
  if (data && !document.hidden && !byId("refresh").disabled && !document.querySelector("dialog[open]")) loadDashboard();
}, 12000);
document.addEventListener("visibilitychange", () => {
  if (data && !document.hidden && !byId("refresh").disabled) loadDashboard();
});
window.addEventListener("hashchange", () => {
  if (location.hash) begin();
});

const standaloneMode = window.matchMedia("(display-mode: standalone)");
const installEntry = byId("install-entry");
const installDialog = byId("install-dialog");
const updateInstallEntry = () => { installEntry.hidden = standaloneMode.matches || navigator.standalone === true; };
let installPrompt;
updateInstallEntry();
standaloneMode.addEventListener?.("change", updateInstallEntry);

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  installPrompt = event;
});
window.addEventListener("appinstalled", () => {
  installPrompt = null;
  installEntry.hidden = true;
});
byId("install-open").addEventListener("click", async () => {
  if (installPrompt) {
    const prompt = installPrompt;
    installPrompt = null;
    try {
      await prompt.prompt();
      return;
    } catch {
      // The browser may withdraw a prompt after it has been queued.
    }
  }
  installDialog.showModal();
});
byId("copy-dashboard-link").addEventListener("click", async () => {
  const address = new URL("/dashboard", location.origin).href;
  try {
    await navigator.clipboard.writeText(address);
    showFormStatus("copy-link-status", "Link copied.");
  } catch {
    const field = byId("dashboard-address");
    field.value = address;
    field.hidden = false;
    field.focus();
    field.select();
    showFormStatus("copy-link-status", "Copy the selected link.");
  }
});

if ("serviceWorker" in navigator && /^\/dashboard\/?$/.test(location.pathname)) {
  navigator.serviceWorker.register("/dashboard-sw.js", { scope: "/dashboard" }).catch(() => {});
}
begin();
