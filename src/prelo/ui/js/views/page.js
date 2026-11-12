// Página: temperatura e demais ajustes, com antes/depois.
// A prévia é renderizada pelo mesmo código que gera o livro.
import { api } from '../api.js';
import { icon } from '../icons.js';
import { bookById, defaultAdjust, on, set, state } from '../store.js';
import { debounce, esc, h, paperColor as paper, toggle } from '../ui.js';

const PRESETS = [
  { id: 'neutro', label: 'Neutro', v: {} },
  { id: 'papel', label: 'Papel', v: { temperature: 5200 } },
  { id: 'sepia', label: 'Sépia', v: { temperature: 4300, saturation: -25, contrast: -5 } },
  { id: 'noite', label: 'Noite', v: { temperature: 3200, brightness: -15 } },
  { id: 'noturno', label: 'Modo noturno', v: { invert: true, temperature: 4200, contrast: -10 } },
  { id: 'eink', label: 'E-ink', v: { grayscale: true, contrast: 15, gamma: 1.35, sharpen: true } },
];

const SLIDERS = [
  { k: 'brightness', label: 'Brilho', min: -100, max: 100, step: 1, fmt: (v) => (v > 0 ? `+${v}` : v) },
  { k: 'contrast', label: 'Contraste', min: -100, max: 100, step: 1, fmt: (v) => (v > 0 ? `+${v}` : v) },
  { k: 'gamma', label: 'Gama', min: 0.3, max: 3, step: 0.05, fmt: (v) => Number(v).toFixed(2).replace('.', ',') },
  { k: 'saturation', label: 'Saturação', min: -100, max: 100, step: 1, fmt: (v) => (v > 0 ? `+${v}` : v) },
];

export default {
  id: 'pagina',
  title: 'Página',
  icon: 'pagina',
  mount(el, ctx) {
    if (ctx.params.book) set('current', ctx.params.book);
    let bookId = state.current || (state.books[0] && state.books[0].id) || null;
    let page = 1;
    let count = 1;
    let split = 50;
    const book0 = bookId ? bookById(bookId) : null;
    let adj = { ...defaultAdjust(), ...(state.adjust || {}) };
    if (!state.adjust && book0) for (const [k, v] of Object.entries(book0.settings || {})) if (k in adj) adj[k] = v;

    if (!state.books.length) {
      el.innerHTML = `<div class="empty" style="height:100%">${icon('pagina')}<div>Adicione um livro à estante para ajustar as páginas.</div></div>`;
      const add = h(`<button class="btn primary">${icon('plus')} Adicionar livros</button>`);
      add.addEventListener('click', () => ctx.open('estante'));
      el.querySelector('.empty').append(add);
      return () => {};
    }

    el.innerHTML = `<div class="editor">
      <div class="stage">
        <div class="compare" id="cmp">
          <img class="before" alt="Página original">
          <img class="after" alt="Página com ajustes">
          <span class="cap l">Original</span><span class="cap r">Ajustada</span>
          <div class="handle" id="handle" role="slider" aria-label="Comparar antes e depois" tabindex="0"></div>
        </div>
        <div class="pager">
          <button class="btn ghost small" id="prev" aria-label="Página anterior">${icon('left')}</button>
          <input id="pnum" class="mono" value="1" aria-label="Página"><span class="label" id="ptotal"></span>
          <button class="btn ghost small" id="next" aria-label="Próxima página">${icon('right')}</button>
        </div>
      </div>
      <aside class="side pad" style="padding-top:20px"><div class="stack" id="ctl"></div></aside>
    </div>`;
    const cmp = el.querySelector('#cmp');
    const before = cmp.querySelector('.before');
    const after = cmp.querySelector('.after');
    const ctl = el.querySelector('#ctl');

    function params(original = false) {
      const w = Math.min(1600, Math.round(cmp.clientHeight * (Number(getComputedStyle(cmp).getPropertyValue('--ratio')) || 0.7) * devicePixelRatio) || 900);
      if (original) return { w, original: true };
      return { w, ...adj };
    }
    const loadAfter = debounce(() => { after.src = api.previewUrl(bookId, page, params()); }, 120);
    function loadPage() {
      before.src = api.previewUrl(bookId, page, params(true));
      after.src = api.previewUrl(bookId, page, params());
      el.querySelector('#pnum').value = page;
    }
    before.addEventListener('load', () => {
      cmp.style.setProperty('--ratio', (before.naturalWidth / before.naturalHeight).toFixed(4));
    });

    async function selectBook(id) {
      bookId = id;
      set('current', id);
      page = 1;
      try { count = (await api.pages(id)).count; } catch (e) { count = 1; }
      el.querySelector('#ptotal').textContent = `de ${count}`;
      loadPage();
    }

    function renderControls() {
      ctl.innerHTML = '';
      const sel = h('<select class="input"></select>');
      for (const b of state.books) sel.append(h(`<option value="${b.id}">${esc(b.metadata?.title || b.title)}</option>`));
      sel.value = bookId;
      sel.addEventListener('change', () => selectBook(sel.value));
      ctl.append(h('<div class="label">Livro</div>'), sel);

      const presets = h('<div class="presets"></div>');
      for (const p of PRESETS) {
        const v = { ...defaultAdjust(), ...p.v };
        const b = h(`<button class="preset"><i style="background:${paper(v)}"></i>${p.label}</button>`);
        b.addEventListener('click', () => { adj = v; renderControls(); loadAfter(); });
        presets.append(b);
      }
      ctl.append(h('<div class="label">Predefinições</div>'), presets);

      const temp = h(`<div class="slider-row"><div class="label"><span>Temperatura</span><b>${adj.temperature} K${adj.temperature === 6500 ? ' · neutra' : adj.temperature < 6500 ? ' · quente' : ' · fria'}</b></div>
        <input type="range" class="temp" min="1900" max="10000" step="100" value="${adj.temperature}" aria-label="Temperatura em kelvin"></div>`);
      const tr = temp.querySelector('input');
      tr.addEventListener('input', () => {
        adj.temperature = Number(tr.value);
        temp.querySelector('b').textContent = `${adj.temperature} K${adj.temperature === 6500 ? ' · neutra' : adj.temperature < 6500 ? ' · quente' : ' · fria'}`;
        loadAfter();
      });
      ctl.append(temp);
      if (adj.grayscale && adj.temperature !== 6500) ctl.append(h('<p class="label" style="line-height:1.4">Em tons de cinza a temperatura não tem efeito.</p>'));

      for (const s of SLIDERS) {
        const row = h(`<div class="slider-row"><div class="label"><span>${s.label}</span><b>${s.fmt(adj[s.k])}</b></div>
          <input type="range" min="${s.min}" max="${s.max}" step="${s.step}" value="${adj[s.k]}" aria-label="${s.label}"></div>`);
        const r = row.querySelector('input');
        r.addEventListener('input', () => { adj[s.k] = Number(r.value); row.querySelector('b').textContent = s.fmt(adj[s.k]); loadAfter(); });
        r.addEventListener('dblclick', () => { adj[s.k] = defaultAdjust()[s.k]; renderControls(); loadAfter(); });
        ctl.append(row);
      }
      const t = (k, label, help) => ctl.append(toggle(label, adj[k], (v) => { adj[k] = v; loadAfter(); if (k === 'grayscale') renderControls(); }, help));
      t('invert', 'Modo noturno', 'Fundo escuro, texto claro, cores preservadas.');
      t('grayscale', 'Tons de cinza', 'Como fica numa tela e-ink.');
      t('sharpen', 'Nitidez', 'Bom para digitalizações.');
      t('normalize', 'Normalizar contraste', 'Estica os tons de páginas apagadas.');

      const actions = h('<div class="stack" style="gap:8px"></div>');
      const save = h(`<button class="btn primary">${icon('check')} Salvar para este livro</button>`);
      save.addEventListener('click', async () => {
        const b = bookById(bookId);
        const settings = { ...(b.settings || {}) };
        for (const [k, v] of Object.entries(adj)) if (k !== 'grayscale') settings[k] = v;
        if (adj.grayscale) settings.color_mode = 'grayscale';
        try {
          await api.patchBook(bookId, { settings });
          await ctx.refreshBooks();
          ctx.toast('Ajustes salvos para este livro', { sub: 'Valem em todas as conversões dele.' });
        } catch (e) { ctx.error(e); }
      });
      const useDefault = h('<button class="btn">Usar como padrão</button>');
      useDefault.addEventListener('click', async () => {
        const saved = { ...(state.settings.saved_defaults || {}) };
        for (const [k, v] of Object.entries(adj)) if (k !== 'grayscale') saved[k] = v;
        try {
          set('settings', await api.saveSettings({ saved_defaults: saved }));
          ctx.toast('Padrão atualizado', { sub: 'Vale para livros sem ajustes próprios.' });
        } catch (e) { ctx.error(e); }
      });
      const convert = h(`<button class="btn">${icon('converter')} Converter com estes ajustes</button>`);
      convert.addEventListener('click', () => { set('adjust', { ...adj }); ctx.open('converter', { select: [bookId], adjust: true }); });
      const reset = h('<button class="btn ghost small">Restaurar original</button>');
      reset.addEventListener('click', () => { adj = defaultAdjust(); renderControls(); loadAfter(); });
      actions.append(save, useDefault, convert, reset);
      ctl.append(actions);
      set('adjust', { ...adj });
    }

    // divisória antes/depois
    const handle = el.querySelector('#handle');
    function setSplit(x) {
      const r = cmp.getBoundingClientRect();
      split = Math.max(0, Math.min(100, ((x - r.left) / r.width) * 100));
      cmp.style.setProperty('--split', `${split}%`);
    }
    cmp.addEventListener('pointerdown', (e) => { cmp.setPointerCapture(e.pointerId); setSplit(e.clientX); });
    cmp.addEventListener('pointermove', (e) => { if (cmp.hasPointerCapture(e.pointerId)) setSplit(e.clientX); });
    handle.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowLeft') split = Math.max(0, split - 5);
      if (e.key === 'ArrowRight') split = Math.min(100, split + 5);
      cmp.style.setProperty('--split', `${split}%`);
    });

    const go = (n) => { page = Math.max(1, Math.min(count, n)); loadPage(); };
    el.querySelector('#prev').addEventListener('click', () => go(page - 1));
    el.querySelector('#next').addEventListener('click', () => go(page + 1));
    el.querySelector('#pnum').addEventListener('change', (e) => go(Number(e.target.value) || 1));
    const offKey = ctx.onKey((e) => {
      if (e.key === 'PageDown') { go(page + 1); return true; }
      if (e.key === 'PageUp') { go(page - 1); return true; }
      return false;
    });
    const ro = new ResizeObserver(debounce(() => loadPage(), 250));
    ro.observe(cmp.parentElement);
    const offBooks = on('books', () => { if (!bookById(bookId) && state.books[0]) selectBook(state.books[0].id); });

    renderControls();
    selectBook(bookId);
    return () => { offKey(); offBooks(); ro.disconnect(); };
  },
};
