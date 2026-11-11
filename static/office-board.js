(() => {
  'use strict';

  if (window.NeoOfficeBoard) return;

  const lanes = {
    inbox: ['Inbox', 'No new receipts.'],
    progress: ['In progress', 'Nothing processing.'],
    review: ['Review', 'No receipts to review.'],
    done: ['Done', 'No completed receipts.'],
  };
  const stages = {
    neo_intake: ['Neo', 'Receiving receipt', 'inbox'],
    corgi_reading: ['Corgi', 'Reading receipt', 'progress'],
    bear_checking: ['Bear', 'Checking receipt', 'progress'],
    girraffe_filing: ['Girraffe', 'Filing receipt', 'progress'],
  };
  const outcomes = {
    recorded: ['Recorded', 'done'],
    duplicate: ['Duplicate detected', 'done'],
    needs_review: ['Needs review', 'review'],
    extraction_unavailable: ['Could not read receipt', 'review'],
    archive_unavailable: ['Could not archive receipt', 'review'],
    error: ['Processing error', 'review'],
  };
  const timestamp = new Intl.DateTimeFormat('en-AU', {
    timeZone: 'Australia/Sydney', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit',
  });

  let tasks = [];
  let signature = '';
  let connection = 'waiting';
  let ui;

  function latestTasks(events) {
    const runs = new Map();
    for (const event of events) {
      if (!event || typeof event !== 'object') continue;
      const { run_id, seq, stage, outcome, at } = event;
      if (typeof run_id !== 'string' || !/^[a-f0-9]{32}$/i.test(run_id)) continue;
      if (!Number.isSafeInteger(seq) || seq < 1) continue;
      if (stage === 'terminal' ? !Object.hasOwn(outcomes, outcome) : !Object.hasOwn(stages, stage)) continue;
      if (typeof at !== 'string' || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(at)) continue;
      const stamp = Date.parse(at);
      if (!Number.isFinite(stamp)) continue;

      const run = runs.get(run_id) || { latest: null, agent: null };
      if (!run.latest || seq > run.latest.seq) run.latest = { seq, stage, outcome, stamp };
      if (stage !== 'terminal' && (!run.agent || seq > run.agent.seq)) {
        run.agent = { seq, name: stages[stage][0] };
      }
      runs.set(run_id, run);
    }

    return [...runs.values()].map(({ latest, agent }) => {
      if (latest.stage === 'terminal') {
        const [title, lane] = outcomes[latest.outcome];
        return { title, lane, agent: agent?.name || 'Neo', stamp: latest.stamp };
      }
      const [name, title, lane] = stages[latest.stage];
      return { title, lane, agent: name, stamp: latest.stamp };
    }).sort((first, second) => second.stamp - first.stamp);
  }

  function update(events) {
    if (!Array.isArray(events)) {
      if (connection === 'offline') return;
      connection = 'offline';
    } else {
      const next = latestTasks(events);
      const nextSignature = JSON.stringify(next);
      if (connection === 'connected' && nextSignature === signature) return;
      connection = 'connected';
      signature = nextSignature;
      tasks = next;
    }
    if (ui?.dialog.open) render();
  }

  function element(tag, attrs = {}, content) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
    if (content !== undefined) node.textContent = content;
    return node;
  }

  function render() {
    const status = {
      waiting: 'Connecting…',
      connected: 'Live receipt activity',
      offline: tasks.length ? 'Offline · Last activity shown' : 'Activity unavailable.',
    }[connection];
    if (ui.status.textContent !== status) ui.status.textContent = status;

    for (const [lane, [, emptyLabel]] of Object.entries(lanes)) {
      const column = ui.columns[lane];
      const items = tasks.filter((task) => task.lane === lane);
      column.count.hidden = !items.length;
      column.count.textContent = items.length;
      column.list.hidden = !items.length;
      column.empty.hidden = Boolean(items.length);
      column.empty.textContent = connection === 'connected' || tasks.length ? emptyLabel : 'No activity loaded.';
      column.list.replaceChildren(...items.map((task) => {
        const card = element('li');
        const time = new Date(task.stamp);
        card.append(
          element('strong', {}, task.title),
          element('span', {}, task.agent),
          element('time', { datetime: time.toISOString() }, timestamp.format(time)),
        );
        return card;
      }));
    }
  }

  function mount() {
    if (ui) return;
    const toolbar = document.querySelector('[data-neo-office-toolbar]') || document.querySelector('header');
    if (!toolbar) return;

    const dialog = element('dialog', { id: 'neo-office-board', class: 'neo-board', 'aria-labelledby': 'neo-board-title' });
    const launch = element('button', {
      type: 'button', class: 'neo-board-launch', 'aria-haspopup': 'dialog', 'aria-controls': dialog.id,
    }, 'Board');
    const close = element('button', { type: 'button' }, 'Close');
    launch.addEventListener('click', () => {
      if (dialog.open) return;
      render();
      dialog.showModal();
      close.focus();
    });
    close.addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => launch.focus({ preventScroll: true }));

    const status = element('p', { class: 'neo-board-status', role: 'status' });
    const heading = element('div');
    heading.append(element('h2', { id: 'neo-board-title' }, 'Board'), status);
    const top = element('div', { class: 'neo-board-top' });
    top.append(heading, close);

    const body = element('div', { class: 'neo-board-body' });
    const columns = {};
    for (const [lane, [label]] of Object.entries(lanes)) {
      const title = element('h3', { id: `neo-board-${lane}` }, label);
      const count = element('span', { class: 'neo-board-count', 'aria-hidden': 'true' });
      const header = element('div', { class: 'neo-board-lane-title' });
      header.append(title, count);
      const section = element('section', { class: 'neo-board-lane', 'aria-labelledby': title.id });
      const empty = element('p', { class: 'neo-board-empty' });
      const list = element('ul');
      section.append(header, empty, list);
      body.append(section);
      columns[lane] = { count, empty, list };
    }
    dialog.append(top, body, element('p', { class: 'neo-board-zone' }, 'Sydney time'));
    ui = { dialog, status, columns };
    document.head.append(element('style', {}, styles));
    document.body.append(dialog);
    const followingAction = toolbar.querySelector('.neo-calendar-launch, .neo-agents-launch');
    toolbar.insertBefore(launch, followingAction);
  }

  const styles = `
    .neo-board, .neo-board-launch {
      --board-ink: #252b27; --board-muted: #52605a; --board-line: #dfe5e0;
      --board-surface: #fbfcfa; --board-accent: #2d6a54;
      color: var(--board-ink); font: 400 0.875rem/1.45 var(--office-font, system-ui, sans-serif);
    }
    .neo-board button, .neo-board-launch {
      box-sizing: border-box; min-height: 2.5rem; padding: 0.4rem 0.7rem;
      border: 1px solid var(--board-line); border-radius: 0.45rem; background: white;
      color: inherit; font: 600 0.8125rem/1.25 var(--office-font, system-ui, sans-serif); cursor: pointer;
    }
    .neo-board button:hover, .neo-board-launch:hover { background: #edf3ef; }
    .neo-board button:focus-visible, .neo-board-launch:focus-visible { outline: 2px solid var(--board-accent); outline-offset: 2px; }
    .neo-board {
      box-sizing: border-box; width: min(60rem, calc(100% - 1rem)); max-width: none;
      max-height: calc(100dvh - 1rem); padding: 0; overflow: auto;
      background: var(--board-surface); border: 1px solid var(--board-line); border-radius: 0.85rem;
    }
    .neo-board::backdrop { background: rgba(23, 33, 28, 0.42); }
    .neo-board h2, .neo-board h3, .neo-board p { margin: 0; }
    .neo-board h2 { font-size: 1.0625rem; font-weight: 600; }
    .neo-board h3 { font-size: 0.875rem; font-weight: 600; }
    .neo-board-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 0.75rem;
      padding: 1rem; border-bottom: 1px solid var(--board-line); }
    .neo-board-status, .neo-board-empty, .neo-board-zone { color: var(--board-muted); font-size: 0.8125rem; }
    .neo-board-body { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.75rem; padding: 1rem; }
    .neo-board-lane { min-width: 0; min-height: 6rem; padding: 0.65rem;
      border: 1px solid var(--board-line); border-radius: 0.55rem; background: #f2f5f2; }
    .neo-board-lane-title { display: flex; align-items: center; justify-content: space-between; gap: 0.3rem;
      margin-bottom: 0.55rem; }
    .neo-board-count { color: var(--board-muted); font-size: 0.75rem; font-variant-numeric: tabular-nums; }
    .neo-board-lane ul { display: grid; gap: 0.5rem; list-style: none; margin: 0; padding: 0; }
    .neo-board-lane ul[hidden] { display: none; }
    .neo-board-lane li { display: grid; gap: 0.12rem; padding: 0.55rem 0.6rem;
      border: 1px solid var(--board-line); border-radius: 0.45rem; background: white; }
    .neo-board-lane strong { font-weight: 600; }
    .neo-board-lane li span, .neo-board-lane time { color: var(--board-muted); font-size: 0.8125rem; }
    .neo-board-lane time { font-variant-numeric: tabular-nums; }
    .neo-board .neo-board-zone { padding: 0 1rem 1rem; }
    @media (max-width: 48rem) { .neo-board-body { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
    @media (max-width: 30rem) {
      .neo-board-top, .neo-board-body { padding: 0.75rem; }
      .neo-board-body { grid-template-columns: minmax(0, 1fr); }
      .neo-board .neo-board-zone { padding-inline: 0.75rem; }
    }
  `;

  window.NeoOfficeBoard = Object.freeze({ update });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
  else mount();
})();
