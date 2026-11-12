// Prelo: desktop, janela central e taskbar.
// Mecânica da janela do PostHog: a janela fica no meio só por flexbox, sem
// barra de título; controles flutuam no canto; Shift+↑ expande, Shift+W fecha;
// duplo clique no cabeçalho alterna o tamanho; cada ícone abre sua função na
// janela em foco.
import { api } from './api.js';
import { desktopReady, isDesktop, reveal, uploadFiles } from './bridge.js';
import { icon } from './icons.js';
import { loadAll, refreshBooks, refreshJobs, set, state } from './store.js';
import { setReduced } from './tween.js';
import { esc, h } from './ui.js';
import convert from './views/convert.js';
import metadata from './views/metadata.js';
import { about, apiView, devices, settingsView } from './views/misc.js';
import page from './views/page.js';
import queue from './views/queue.js';
import reader from './views/reader.js';
import shelf, { addBooks } from './views/shelf.js';

const VIEWS = Object.fromEntries([shelf, reader, convert, page, metadata, queue, devices, apiView, settingsView, about].map((v) => [v.id, v]));
const LEFT = ['estante', 'leitor', 'converter', 'pagina', 'metadados', 'fila'];
const RIGHT = ['dispositivos', 'api', 'preferencias', 'sobre'];

const $ = (s) => document.querySelector(s);
const win = $('#win');
const body = $('#aw-body');
const app = $('#app');
let current = null;
let unmount = null;
let closed = false;
const keyHandlers = new Set();
const themeHandlers = new Set();
let watchers = 0;

// ------------------------------------------------------------- notificações
function toast(message, { sub = '', kind = '', action = null, ms = 4200 } = {}) {
  const t = h(`<div class="toast ${kind}" role="status"><div>${esc(message)}</div>${sub ? `<small>${esc(sub)}</small>` : ''}</div>`);
  if (action) {
    const b = h(`<button class="btn small" style="justify-self:start;margin-top:4px;background:transparent;color:inherit;border-color:currentColor">${esc(action.label)}</button>`);
    b.addEventListener('click', () => { action.fn(); dismiss(); });
    t.append(b);
  }
  const dismiss = () => { t.classList.add('out'); setTimeout(() => t.remove(), 200); };
  $('#toasts').append(t);
  setTimeout(dismiss, ms);
}
function error(e) {
  console.error(e);
  toast(e.message || 'Algo deu errado.', { kind: 'err', sub: e.hint || '', ms: 7000 });
}

// ------------------------------------------------------------- modal
function modal(title, content, buttons = []) {
  const overlay = h('<div class="overlay"></div>');
  const m = h(`<div class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
    <header class="aw-head" style="padding-right:18px"><div class="aw-title">${esc(title)}</div></header>
    <div class="aw-body"></div>
    <div class="row" style="justify-content:flex-end;padding:0 14px 14px"></div></div>`);
  m.querySelector('.aw-body').append(content);
  const close = () => { overlay.remove(); m.remove(); keyHandlers.delete(onKey); };
  const row = m.querySelector('.row');
  for (const b of [{ label: 'Cancelar' }, ...buttons]) {
    const el = h(`<button class="btn ${b.primary ? 'primary' : ''}">${esc(b.label)}</button>`);
    el.addEventListener('click', () => { if (b.fn) b.fn(); close(); });
    row.append(el);
  }
  const onKey = (e) => { if (e.key === 'Escape') { close(); return true; } return false; };
  keyHandlers.add(onKey);
  overlay.addEventListener('click', close);
  document.body.append(overlay, m);
}

// ------------------------------------------------------------- janela
function ctxFor(id, params) {
  return {
    params,
    open,
    toast,
    error,
    modal,
    refreshBooks,
    refreshJobs: () => refreshJobs().then(updateJobsChip),
    applySettings,
    expand: (on) => toggleExpand(on),
    expanded: () => win.dataset.state === 'expanded',
    watchJobs: (on) => { watchers += on ? 1 : -1; },
    onKey: (fn) => {
      const wrapped = (e) => current === id && !closed && fn(e);
      keyHandlers.add(wrapped);
      return () => keyHandlers.delete(wrapped);
    },
    onTheme: (fn) => { themeHandlers.add(fn); return () => themeHandlers.delete(fn); },
  };
}

function open(id, params = {}) {
  const view = VIEWS[id];
  if (!view) return;
  if (closed) {
    closed = false;
    win.hidden = false;
    win.classList.remove('pop-out');
    win.classList.add('pop-in');
    win.addEventListener('animationend', () => win.classList.remove('pop-in'), { once: true });
    $('#desk-hint').hidden = true;
  }
  if (unmount) { try { unmount(); } catch (e) { console.error(e); } }
  body.innerHTML = '';
  const el = document.createElement('div');
  el.className = 'view enter';
  body.append(el);
  $('#aw-title').innerHTML = `<span class="crumb">Prelo /</span><span>${esc(view.title)}</span>`;
  $('#aw-actions').innerHTML = '';
  current = id;
  win.dataset.view = id;
  document.querySelectorAll('.desk-icon').forEach((b) => b.setAttribute('aria-current', String(b.dataset.open === id)));
  try { localStorage.setItem('prelo.view', id); } catch (e) { /* sem armazenamento */ }
  try {
    unmount = view.mount(el, ctxFor(id, params)) || null;
  } catch (e) { error(e); }
}

function closeWindow() {
  if (closed) return;
  win.classList.add('pop-out');
  win.addEventListener('animationend', () => {
    if (unmount) { unmount(); unmount = null; }
    body.innerHTML = '';
    win.hidden = true;
    closed = true;
    current = null;
    $('#desk-hint').hidden = false;
    document.querySelectorAll('.desk-icon').forEach((b) => b.setAttribute('aria-current', 'false'));
  }, { once: true });
}

function toggleExpand(force) {
  const expanded = force ?? win.dataset.state !== 'expanded';
  win.dataset.state = expanded ? 'expanded' : 'windowed';
  app.toggleAttribute('data-window-expanded', expanded);
  win.querySelector('[data-act="expand"]').innerHTML = icon(expanded ? 'collapse' : 'expand');
}

// ------------------------------------------------------------- desktop
function buildIcons() {
  const mk = (id) => {
    const v = VIEWS[id];
    const b = h(`<button class="desk-icon" data-open="${id}" aria-current="false">${icon(v.icon)}<span>${esc(v.title)}</span></button>`);
    b.addEventListener('click', (e) => { e.stopPropagation(); open(id); });
    return b;
  };
  LEFT.forEach((id) => $('#icons-left').append(mk(id)));
  RIGHT.forEach((id) => $('#icons-right').append(mk(id)));
}

function selectionBox() {
  const desk = $('#desktop');
  const box = $('#selbox');
  let start = null;
  desk.addEventListener('pointerdown', (e) => {
    if (e.target !== desk && !e.target.classList.contains('window-list')) return;
    const r = desk.getBoundingClientRect();
    start = { x: e.clientX - r.left, y: e.clientY - r.top, r };
    desk.setPointerCapture(e.pointerId);
    document.querySelectorAll('.desk-icon').forEach((i) => i.classList.remove('selected'));
  });
  desk.addEventListener('pointermove', (e) => {
    if (!start) return;
    const x = e.clientX - start.r.left;
    const y = e.clientY - start.r.top;
    const l = Math.min(x, start.x); const t = Math.min(y, start.y);
    const w = Math.abs(x - start.x); const hh = Math.abs(y - start.y);
    if (w < 4 && hh < 4) return;
    Object.assign(box.style, { left: `${l}px`, top: `${t}px`, width: `${w}px`, height: `${hh}px` });
    box.hidden = false;
    document.querySelectorAll('.desk-icon').forEach((i) => {
      const ir = i.getBoundingClientRect();
      const il = ir.left - start.r.left; const it = ir.top - start.r.top;
      const hit = il < l + w && il + ir.width > l && it < t + hh && it + ir.height > t;
      i.classList.toggle('selected', hit);
    });
  });
  desk.addEventListener('pointerup', () => { start = null; box.hidden = true; });
}

// ------------------------------------------------------------- taskbar
const MENUS = {
  prelo: [['Sobre o Prelo', () => open('sobre')], ['Preferências…', () => open('preferencias')], '-',
    ['Sair', quitApp]],
  arquivo: [['Adicionar livros…', () => addBooks(ctxFor(current, {})).then(() => open('estante')), 'Ctrl+O'],
    ['Ler', () => open('leitor')], ['Converter…', () => open('converter')], '-', ['Estante', () => open('estante')],
    ['Fila de conversões', () => open('fila')]],
  exibir: [['Expandir ou restaurar', () => toggleExpand(), 'Shift+↑'], ['Fechar janela', closeWindow, 'Shift+W'], '-',
    ['Tema claro', () => setTheme('light')], ['Tema escuro', () => setTheme('dark')], ['Tema do sistema', () => setTheme('system')]],
  ajuda: [['Documentação da API', () => window.open('/api/docs', '_blank')], ['Dispositivos e resoluções', () => open('dispositivos')],
    ['Atalhos', () => toast('Atalhos', { sub: 'Shift+↑ expandir · Shift+W fechar · Ctrl+O adicionar · ←/→ estante · Esc voltar · '
      + 'Leitor: ←/→ ou espaço vira a página, +/− letra ou zoom, T sumário, Home/End início e fim', ms: 9000 })]],
};
async function quitApp() {
  if (isDesktop()) { window.pywebview.api.quit(); return; }
  try { await api.quit(); } catch (e) { error(e); return; }
  document.body.innerHTML = '<div class="empty" style="height:100vh">O Prelo foi encerrado. Pode fechar esta aba.</div>';
}
let openMenu = null;
function closeMenu() {
  if (openMenu) { openMenu.el.remove(); openMenu.btn.setAttribute('aria-expanded', 'false'); openMenu = null; }
}
function setupMenus() {
  document.querySelectorAll('[data-menu]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const same = openMenu && openMenu.btn === btn;
      closeMenu();
      if (same) return;
      const m = h('<div class="menu" role="menu"></div>');
      for (const item of MENUS[btn.dataset.menu]) {
        if (item === '-') { m.append(h('<hr>')); continue; }
        const [label, fn, kbd] = item;
        const b = h(`<button role="menuitem"><span>${esc(label)}</span>${kbd ? `<kbd>${esc(kbd)}</kbd>` : ''}</button>`);
        b.addEventListener('click', () => { closeMenu(); fn(); });
        m.append(b);
      }
      const r = btn.getBoundingClientRect();
      m.style.left = `${r.left - 8}px`;
      $('.taskbar').append(m);
      btn.setAttribute('aria-expanded', 'true');
      openMenu = { el: m, btn };
    });
  });
  document.addEventListener('click', closeMenu);
}

function effectiveTheme() {
  const t = state.settings.theme || 'system';
  if (t !== 'system') return t;
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
function applySettings() {
  const s = state.settings;
  const t = s.theme || 'system';
  if (t === 'system') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  document.documentElement.toggleAttribute('data-reduce-motion', Boolean(s.reduce_motion));
  setReduced(Boolean(s.reduce_motion));
  try {
    localStorage.setItem('prelo.theme', t);
    localStorage.setItem('prelo.reduceMotion', s.reduce_motion ? '1' : '0');
  } catch (e) { /* sem armazenamento */ }
  $('#tb-theme').innerHTML = icon(effectiveTheme() === 'dark' ? 'sun' : 'moon');
  themeHandlers.forEach((fn) => fn());
}
async function setTheme(theme) {
  try { set('settings', await api.saveSettings({ theme })); applySettings(); } catch (e) { error(e); }
}

function clock() {
  const d = new Date();
  $('#tb-clock').textContent = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

let lastStatus = new Map();
function updateJobsChip() {
  const active = state.jobs.filter((j) => j.status === 'running' || j.status === 'queued');
  const chip = $('#tb-jobs');
  chip.hidden = !active.length;
  if (active.length) {
    const run = active.find((j) => j.status === 'running');
    chip.querySelector('span:last-child').textContent = run ? `${Math.round(run.progress * 100)}% · ${active.length} na fila` : `${active.length} na fila`;
  }
  for (const j of state.jobs) {
    const before = lastStatus.get(j.id);
    if (before && before !== j.status) {
      if (j.status === 'done') {
        const name = j.result.output_path.split(/[\\/]/).pop();
        toast(`Pronto: ${name}`, { sub: j.result.notes?.[0] || '', action: isDesktop() ? { label: 'Mostrar na pasta', fn: () => reveal(j.result.output_path) } : { label: 'Baixar', fn: () => { location.href = api.downloadUrl(j.id); } } });
      } else if (j.status === 'error') {
        toast(`Falhou: ${j.title}`, { kind: 'err', sub: j.error?.message || '' });
      }
    }
    lastStatus.set(j.id, j.status);
  }
}

async function poll() {
  const busy = state.jobs.some((j) => j.status === 'running' || j.status === 'queued');
  try {
    if (busy || watchers > 0) { await refreshJobs(); updateJobsChip(); }
    $('#tb-api').querySelector('.dot').style.opacity = 1;
  } catch (e) {
    $('#tb-api').querySelector('span:last-child').textContent = 'API offline';
    $('#tb-api').querySelector('.dot').style.opacity = 0.2;
  }
  setTimeout(poll, busy ? 700 : 2500);
}

// ------------------------------------------------------------- arrastar e soltar
// Como no Calibre (calibre.gui2.dnd), aceita arquivos, listas de URLs e o que o
// Firefox entrega ao arrastar do painel de downloads ou de uma página.
const DROP_TYPES = ['Files', 'text/uri-list', 'text/x-moz-url', 'application/x-moz-file'];
let pythonDropAt = 0;

function droppedUris(dt) {
  const out = [];
  const add = (s) => {
    s = (s || '').trim();
    if (s && !s.startsWith('#') && /^(file|https?):\/\//i.test(s) && !out.includes(s)) out.push(s);
  };
  const read = (type) => { try { return dt.getData(type) || ''; } catch { return ''; } };
  read('text/uri-list').split(/\r?\n/).forEach(add);
  const moz = read('text/x-moz-url').split(/\r?\n/);  // URL e título, alternados
  for (let i = 0; i < moz.length; i += 2) add(moz[i]);
  if (!out.length) add(read('text/plain'));
  return out;
}

function dragAndDrop() {
  const overlay = $('#drop');
  let depth = 0;
  const droppable = (e) => [...(e.dataTransfer?.types || [])].some((t) => DROP_TYPES.includes(t));
  window.addEventListener('dragenter', (e) => { if (!droppable(e)) return; depth += 1; overlay.hidden = false; });
  window.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) overlay.hidden = true; });
  window.addEventListener('dragover', (e) => {
    if (!droppable(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });
  window.addEventListener('drop', async (e) => {
    if (!droppable(e)) return;
    e.preventDefault();
    depth = 0;
    overlay.hidden = true;
    const started = Date.now();
    const files = [...(e.dataTransfer.files || [])];
    const uris = droppedUris(e.dataTransfer);
    try {
      if (isDesktop()) {
        // O Python costuma receber o caminho completo pelo GTK ou pelo WebView2 e avisa (prelo.dropHandled).
        // Se não avisar, usamos as URLs que a página conseguiu ler.
        while (Date.now() - started < 900 && pythonDropAt < started - 200) await new Promise((r) => setTimeout(r, 60));
        if (pythonDropAt >= started - 200) return;
        if (uris.length) {
          if (uris.some((u) => /^https?:/i.test(u))) toast('Baixando o livro…', { sub: uris[0] });
          window.prelo.booksAdded(await window.pywebview.api.add_uris(uris));
          return;
        }
      }
      if (files.length) {
        window.prelo.booksAdded(await uploadFiles(files));
        return;
      }
      toast('Nada para adicionar', {
        kind: 'err',
        sub: uris.length ? 'Pelo navegador só dá para adicionar arquivos, não links. Baixe o livro e arraste o arquivo.'
          : 'Arraste arquivos de livro (PDF, EPUB, AZW3, MOBI, CBZ) ou use Adicionar livros.',
      });
    } catch (err) { error(err); }
  });
}

window.prelo = {
  dropHandled() { pythonDropAt = Date.now(); },
  async booksAdded(res) {
    await refreshBooks();
    if (res.added && res.added.length) {
      toast(`${res.added.length} livro(s) na estante`, { sub: res.added.map((b) => b.title).slice(0, 3).join(' · ') });
      if (current !== 'converter') open('estante', { book: res.added[0].id });
    }
    for (const e of res.errors || []) {
      toast(e.message, { kind: 'err', sub: [e.hint, (e.path || '').split(/[\\/]/).pop()].filter(Boolean).join(' · ') });
    }
  },
};

// ------------------------------------------------------------- teclado
function keyboard() {
  window.addEventListener('keydown', (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '');
    if (e.shiftKey && e.key === 'ArrowUp' && !typing) { e.preventDefault(); toggleExpand(); return; }
    if (e.shiftKey && (e.key === 'W' || e.key === 'w') && !typing) { e.preventDefault(); closeWindow(); return; }
    if ((e.ctrlKey || e.metaKey) && (e.key === 'o' || e.key === 'O')) { e.preventDefault(); MENUS.arquivo[0][1](); return; }
    if (e.key === 'Escape') closeMenu();
    if (typing) return;
    for (const fn of [...keyHandlers].reverse()) if (fn(e)) { e.preventDefault(); break; }
  });
}

// ------------------------------------------------------------- início
async function boot() {
  $('#brand-mark').innerHTML = icon('logo');
  buildIcons();
  win.querySelector('[data-act="close"]').innerHTML = icon('close');
  win.querySelector('[data-act="close"]').addEventListener('click', closeWindow);
  win.querySelector('[data-act="expand"]').addEventListener('click', () => toggleExpand());
  win.querySelector('.aw-head').addEventListener('dblclick', () => toggleExpand());
  toggleExpand(false);
  setupMenus();
  selectionBox();
  dragAndDrop();
  keyboard();
  clock();
  setInterval(clock, 15000);
  $('#tb-api').querySelector('span:last-child').textContent = `API · ${location.host}`;
  $('#tb-jobs').addEventListener('click', () => open('fila'));
  $('#tb-theme').addEventListener('click', () => setTheme(effectiveTheme() === 'dark' ? 'light' : 'dark'));
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => applySettings());
  try {
    await Promise.all([loadAll(), desktopReady()]);
  } catch (e) {
    error(e);
    return;
  }
  applySettings();
  for (const j of state.jobs) lastStatus.set(j.id, j.status);
  updateJobsChip();
  // link direto: #view=pagina&book=ID abre a janela e o livro
  const hash = new URLSearchParams(location.hash.slice(1));
  let first = hash.get('view');
  if (!first) {
    try { first = localStorage.getItem('prelo.view'); } catch (e) { /* padrão */ }
  }
  const params = hash.get('book') ? { book: hash.get('book'), select: [hash.get('book')] } : {};
  open(VIEWS[first] ? first : 'estante', params);
  window.addEventListener('hashchange', () => {
    const p = new URLSearchParams(location.hash.slice(1));
    const v = p.get('view');
    if (VIEWS[v]) open(v, p.get('book') ? { book: p.get('book'), select: [p.get('book')] } : {});
  });
  poll();
}

boot();
