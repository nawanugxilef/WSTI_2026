(() => {
  'use strict';

  if (window.NeoOfficeCalendar) return;

  const SYDNEY = 'Australia/Sydney';
  const stages = {
    neo_intake: ['Neo', 'Receiving receipt'],
    corgi_reading: ['Corgi', 'Reading receipt'],
    bear_checking: ['Bear', 'Checking receipt'],
    girraffe_filing: ['Girraffe', 'Filing receipt'],
  };
  const outcomes = {
    recorded: 'Recorded',
    needs_review: 'Needs review',
    duplicate: 'Duplicate detected',
    extraction_unavailable: 'Could not read receipt',
    archive_unavailable: 'Could not archive receipt',
    error: 'Processing error',
  };
  const weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  const sydneyDay = new Intl.DateTimeFormat('en-AU', { timeZone: SYDNEY, year: 'numeric', month: '2-digit', day: '2-digit' });
  const sydneyTime = new Intl.DateTimeFormat('en-AU', { timeZone: SYDNEY, timeStyle: 'short' });
  const monthLabel = new Intl.DateTimeFormat('en-AU', { timeZone: 'UTC', year: 'numeric', month: 'long' });
  const dayLabel = new Intl.DateTimeFormat('en-AU', { timeZone: 'UTC', dateStyle: 'full' });

  function sydneyDateKey(date) {
    const parts = Object.fromEntries(sydneyDay.formatToParts(date).map(({ type, value }) => [type, value]));
    return `${parts.year}-${parts.month.padStart(2, '0')}-${parts.day.padStart(2, '0')}`;
  }

  const dateFor = (key) => new Date(`${key}T00:00:00Z`);
  const dateKey = (date) => date.toISOString().slice(0, 10);
  let selectedDay = sydneyDateKey(new Date());
  let agentFilter = '';
  let connection = 'waiting';
  let lastSnapshot = '';
  let activity = [];
  let ui;

  function readSnapshot(events) {
    const byRun = new Map();
    for (const entry of events) {
      if (!entry || typeof entry !== 'object') continue;
      const { run_id, seq, stage, at, outcome } = entry;
      if (typeof run_id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(run_id)) continue;
      if (!Number.isSafeInteger(seq) || seq < 0) continue;
      if (typeof stage !== 'string' || (stage !== 'terminal' && !Object.hasOwn(stages, stage))) continue;
      if (typeof at !== 'string' || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(at)) continue;
      const stamp = Date.parse(at);
      if (!Number.isFinite(stamp)) continue;
      const run = byRun.get(run_id) || new Map();
      run.set(seq, { seq, stage, stamp, outcome: typeof outcome === 'string' && Object.hasOwn(outcomes, outcome) ? outcome : '' });
      byRun.set(run_id, run);
    }
    const rows = [];
    for (const run of byRun.values()) {
      let agent = stages.neo_intake[0];
      for (const row of [...run.values()].sort((a, b) => a.seq - b.seq)) {
        const terminal = row.stage === 'terminal';
        const label = terminal ? outcomes[row.outcome] || 'Processing ended' : stages[row.stage][1];
        if (!terminal) agent = stages[row.stage][0];
        rows.push({ agent, label, stamp: row.stamp, day: sydneyDateKey(new Date(row.stamp)), terminal });
      }
    }
    return rows.sort((a, b) => a.stamp - b.stamp);
  }

  function update(events) {
    if (!Array.isArray(events)) {
      if (connection === 'offline') return;
      connection = 'offline';
    } else {
      const next = readSnapshot(events);
      const signature = JSON.stringify(next);
      if (connection === 'connected' && signature === lastSnapshot) return;
      connection = 'connected';
      lastSnapshot = signature;
      activity = next;
    }
    if (ui?.dialog.open) render();
  }

  function element(tag, attrs = {}, content) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
    if (content !== undefined) node.textContent = content;
    return node;
  }

  function button(text, action, attrs = {}) {
    const control = element('button', { type: 'button', ...attrs }, text);
    control.addEventListener('click', action);
    return control;
  }

  function setText(node, content) {
    if (node.textContent !== content) node.textContent = content;
  }

  function stepMonth(key, direction) {
    const date = dateFor(key);
    const month = date.getUTCMonth() + direction;
    const year = date.getUTCFullYear();
    const lastDay = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
    return dateKey(new Date(Date.UTC(year, month, Math.min(date.getUTCDate(), lastDay))));
  }

  function chooseDay(key, focusDay = false) {
    selectedDay = key;
    render(focusDay);
  }

  function onDayKey(event) {
    const day = event.target.closest('button[data-day]');
    if (!day) return;
    const key = day.dataset.day;
    const date = dateFor(key);
    const weekday = (date.getUTCDay() + 6) % 7;
    const offsets = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7, Home: -weekday, End: 6 - weekday };
    if (Object.hasOwn(offsets, event.key)) {
      date.setUTCDate(date.getUTCDate() + offsets[event.key]);
      chooseDay(dateKey(date), true);
    } else if (event.key === 'PageUp' || event.key === 'PageDown') {
      chooseDay(stepMonth(key, event.key === 'PageUp' ? -1 : 1), true);
    } else {
      return;
    }
    event.preventDefault();
  }

  function renderMonth(byDay, today, focusDay) {
    const hadDayFocus = ui.grid.contains(document.activeElement);
    const date = dateFor(selectedDay);
    const year = date.getUTCFullYear();
    const month = date.getUTCMonth();
    const first = new Date(Date.UTC(year, month, 1));
    const offset = (first.getUTCDay() + 6) % 7;
    const days = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
    const rows = [];
    let selectedButton;
    for (let i = 0; i < offset + days; i += 7) {
      const row = element('tr', { role: 'row' });
      for (let col = 0; col < 7; col++) {
        const day = i + col + 1 - offset;
        const cell = element('td', { role: 'gridcell' });
        row.append(cell);
        if (day < 1 || day > days) continue;
        const key = dateKey(new Date(Date.UTC(year, month, day)));
        const selected = key === selectedDay;
        const count = byDay.get(key)?.length || 0;
        const control = button(String(day), () => chooseDay(key, true), {
          'data-day': key,
          'aria-label': `${dayLabel.format(dateFor(key))}, ${count} ${count === 1 ? 'activity' : 'activities'}`,
          tabindex: selected ? '0' : '-1',
        });
        cell.setAttribute('aria-selected', String(selected));
        if (key === today) control.setAttribute('aria-current', 'date');
        if (count) control.append(element('span', { class: 'neo-calendar-count', 'aria-hidden': 'true' }, String(count)));
        if (selected) selectedButton = control;
        cell.append(control);
      }
      rows.push(row);
    }
    setText(ui.month, monthLabel.format(first));
    ui.days.replaceChildren(...rows);
    if (focusDay || hadDayFocus) selectedButton?.focus({ preventScroll: true });
  }

  function render(focusDay = false) {
    const byDay = new Map();
    for (const event of activity) {
      if (agentFilter && event.agent !== agentFilter) continue;
      const day = byDay.get(event.day) || [];
      day.push(event);
      byDay.set(event.day, day);
    }
    const status = {
      waiting: 'Waiting for Office activity.',
      connected: activity.length ? 'Connected to Office activity.' : 'Connected. No receipt activity yet.',
      offline: activity.length ? 'Connection unavailable. Showing the last activity received.' : 'Office activity is unavailable.',
    }[connection];
    setText(ui.status, status);
    renderMonth(byDay, sydneyDateKey(new Date()), focusDay);
    setText(ui.date, dayLabel.format(dateFor(selectedDay)));
    const entries = byDay.get(selectedDay) || [];
    const empty = connection === 'connected' || activity.length ? 'No matching activity on this day.' : 'No activity has loaded.';
    ui.empty.textContent = entries.length ? '' : empty;
    ui.list.hidden = !entries.length;
    ui.list.replaceChildren(...entries.map((event) => {
      const item = element('li', { class: event.terminal ? 'neo-calendar-terminal' : 'neo-calendar-event' });
      const time = new Date(event.stamp);
      const detail = element('div');
      detail.append(element('strong', {}, event.label), element('small', {}, event.agent));
      item.append(element('time', { datetime: time.toISOString() }, sydneyTime.format(time)), detail);
      return item;
    }));
  }

  function mount() {
    if (ui) return;
    const toolbar = document.querySelector('[data-neo-office-toolbar]') || document.querySelector('header');
    if (!toolbar) return;

    const dialog = element('dialog', { id: 'neo-office-calendar', class: 'neo-calendar', 'aria-labelledby': 'neo-calendar-title' });
    const launch = button('Calendar', () => {
      if (dialog.open) return;
      render();
      launch.focus({ preventScroll: true });
      dialog.showModal();
      close.focus();
    }, { class: 'neo-calendar-launch', 'aria-haspopup': 'dialog', 'aria-controls': dialog.id });
    const close = button('Close', () => dialog.close());
    dialog.addEventListener('close', () => launch.focus({ preventScroll: true }));

    const top = element('div', { class: 'neo-calendar-top' });
    const title = element('h2', { id: 'neo-calendar-title' }, 'Activity calendar');
    const status = element('p', { class: 'neo-calendar-status', role: 'status' });
    const heading = element('div');
    heading.append(title, status);
    top.append(heading, close);

    const body = element('div', { class: 'neo-calendar-body' });
    const monthPanel = element('section', { 'aria-labelledby': 'neo-calendar-month' });
    const nav = element('div', { class: 'neo-calendar-nav' });
    const month = element('h3', { id: 'neo-calendar-month', 'aria-live': 'polite' });
    const filter = element('label', { class: 'neo-calendar-filter' }, 'Agent');
    const select = element('select');
    for (const agent of ['', ...Object.values(stages).map(([name]) => name)]) {
      select.append(element('option', { value: agent }, agent || 'All agents'));
    }
    select.addEventListener('change', () => { agentFilter = select.value; render(); });
    filter.append(select);
    nav.append(
      button('‹', () => chooseDay(stepMonth(selectedDay, -1)), { 'aria-label': 'Previous month' }),
      month,
      button('›', () => chooseDay(stepMonth(selectedDay, 1)), { 'aria-label': 'Next month' }),
      button('Today', () => chooseDay(sydneyDateKey(new Date())))
    );

    const grid = element('table', { class: 'neo-calendar-grid', role: 'grid', 'aria-labelledby': month.id });
    const tableHead = element('thead', { role: 'rowgroup' });
    const week = element('tr', { role: 'row' });
    for (const day of weekdays) week.append(element('th', { scope: 'col', role: 'columnheader', 'aria-label': day }, day.slice(0, 3)));
    tableHead.append(week);
    const days = element('tbody', { role: 'rowgroup' });
    grid.append(tableHead, days);
    grid.addEventListener('keydown', onDayKey);
    monthPanel.append(nav, filter, grid);

    const details = element('section', { class: 'neo-calendar-details', 'aria-labelledby': 'neo-calendar-date' });
    const date = element('h3', { id: 'neo-calendar-date', 'aria-live': 'polite' });
    const empty = element('p', { class: 'neo-calendar-empty' });
    const list = element('ol', { class: 'neo-calendar-list' });
    details.append(date, element('p', { class: 'neo-calendar-zone' }, 'Times in Australia/Sydney'), empty, list);
    body.append(monthPanel, details);
    dialog.append(top, body);
    ui = { dialog, status, month, grid, days, date, empty, list };
    document.head.append(element('style', {}, calendarStyles));
    document.body.append(dialog);
    toolbar.append(launch);
  }

  const calendarStyles = `
    .neo-calendar, .neo-calendar-launch {
      --cal-surface: #171e33; --cal-border: #465579; --cal-hover: #263556;
      --cal-text: #f0f3fa; --cal-muted: #bac6df; --cal-accent: #a9c9ff;
      font: inherit; color: var(--cal-text);
    }
    .neo-calendar button, .neo-calendar select, .neo-calendar-launch {
      box-sizing: border-box; background: #222e49; color: inherit; font: inherit;
      border: 1px solid var(--cal-border); border-radius: 0.5rem;
      min-height: 2.5rem; padding: 0.4rem 0.65rem; cursor: pointer;
    }
    .neo-calendar button:hover, .neo-calendar-launch:hover { background: var(--cal-hover); }
    .neo-calendar button:focus-visible, .neo-calendar select:focus-visible, .neo-calendar-launch:focus-visible {
      outline: 2px solid var(--cal-accent); outline-offset: 2px;
    }
    .neo-calendar-launch { margin-inline-start: auto; }
    .neo-calendar {
      box-sizing: border-box; width: min(51rem, calc(100% - 1rem)); max-width: none;
      max-height: calc(100dvh - 1rem); padding: 0; overflow: auto;
      background: var(--cal-surface); border: 1px solid var(--cal-border); border-radius: 1rem;
    }
    .neo-calendar::backdrop { background: rgba(6, 10, 22, 0.74); }
    .neo-calendar-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 0.75rem;
      padding: 1rem; border-bottom: 1px solid var(--cal-border); }
    .neo-calendar h2, .neo-calendar h3, .neo-calendar p { margin: 0; }
    .neo-calendar h2 { font-size: 1.25rem; }
    .neo-calendar h3 { font-size: 1rem; }
    .neo-calendar-status, .neo-calendar-zone, .neo-calendar-empty { color: var(--cal-muted); }
    .neo-calendar-status, .neo-calendar-zone { font-size: 0.82rem; }
    .neo-calendar-body { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 0.9fr); gap: 1rem; padding: 1rem; }
    .neo-calendar-nav { display: grid; grid-template-columns: 2.5rem minmax(0, 1fr) 2.5rem auto; gap: 0.35rem; align-items: center; }
    .neo-calendar-nav h3 { text-align: center; }
    .neo-calendar-nav button[aria-label] { font-size: 1.35rem; line-height: 1; }
    .neo-calendar-filter { display: flex; gap: 0.5rem; align-items: center; margin: 0.65rem 0 0.25rem; }
    .neo-calendar-filter select { padding-inline-end: 1.6rem; }
    .neo-calendar-grid { width: 100%; table-layout: fixed; border-spacing: 0.18rem; }
    .neo-calendar-grid th { font-size: 0.75rem; color: var(--cal-muted); padding-block: 0.25rem; }
    .neo-calendar-grid td { padding: 0; }
    .neo-calendar-grid button { display: flex; flex-direction: column; align-items: center;
      justify-content: center; width: 100%; min-width: 0; min-height: 2.6rem; padding: 0; }
    .neo-calendar-grid [aria-current='date'] { border-color: var(--cal-accent); }
    .neo-calendar-grid [aria-selected='true'] button { background: #b7d2ff; color: #172238; border-color: #b7d2ff; font-weight: 700; }
    .neo-calendar-count { font-size: 0.68rem; line-height: 1; opacity: 0.95; }
    .neo-calendar-details { padding-inline-start: 1rem; border-inline-start: 1px solid var(--cal-border); min-width: 0; }
    .neo-calendar-details .neo-calendar-empty { margin-top: 0.7rem; }
    .neo-calendar-list { list-style: none; margin: 0.55rem 0 0; padding: 0; }
    .neo-calendar-list li { display: grid; grid-template-columns: 5.4rem minmax(0, 1fr); gap: 0.5rem;
      padding: 0.48rem 0; border-bottom: 1px solid var(--cal-border); }
    .neo-calendar-list time, .neo-calendar-list small { color: var(--cal-muted); font-variant-numeric: tabular-nums; }
    .neo-calendar-list small { display: block; }
    .neo-calendar-terminal strong { color: var(--cal-accent); }
    @media (max-width: 40rem) {
      .neo-calendar-launch { margin-inline-start: 0; }
      .neo-calendar-top, .neo-calendar-body { padding: 0.75rem; }
      .neo-calendar-body { grid-template-columns: minmax(0, 1fr); gap: 0.75rem; }
      .neo-calendar-details { padding: 0.75rem 0 0; border-inline-start: 0; border-top: 1px solid var(--cal-border); }
    }
  `;

  window.NeoOfficeCalendar = Object.freeze({ update });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
  else mount();
})();
