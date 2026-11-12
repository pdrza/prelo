// Leitor: lê qualquer livro da estante (EPUB, PDF, Kindle, quadrinhos, imagens).
// As páginas vêm prontas da API, pelo mesmo motor da prévia (core/reader.py);
// aqui ficam a geometria na tela, os atalhos, o sumário, a letra, o tema e o
// "onde parou".
import { api } from '../api.js';
import { icon } from '../icons.js';
import { bookById, set, state } from '../store.js';
import { debounce, esc, h, paperColor, segmented } from '../ui.js';

// Temas: os mesmos ajustes de página do editor; quem pinta a página é o motor.
const THEMES = [
  { id: 'claro', label: 'Claro', adj: {} },
  { id: 'papel', label: 'Papel', adj: { temperature: 5200 } },
  { id: 'sepia', label: 'Sépia', adj: { temperature: 4300, saturation: -25, contrast: -5 } },
  { id: 'noite', label: 'Noite', adj: { invert: true, temperature: 4200, contrast: -10 } },
];
const SIZES = [12, 14, 16, 18, 20, 22, 24, 28, 32, 36, 42];  // letra, em pixels (livros fluidos)
const ZOOMS = [1, 1.25, 1.5, 2, 3];                         // zoom (livros fixos)
const SPREADS = [{ value: 'auto', label: 'Auto' }, { value: '1', label: '1' }, { value: '2', label: '2' }];

// Preferências do leitor ficam nas preferências do Prelo (a API): a janela do app não
// guarda localStorage entre uma abertura e outra.
const prefs = () => ({ theme: 'claro', spread: 'auto', font_size: 18, ...(state.settings.reader || {}) });
async function savePrefs(patch) {
  const reader = { ...prefs(), ...patch };
  state.settings.reader = reader;  // já vale nesta sessão, mesmo se a API não responder
  try { set('settings', await api.saveSettings({ reader })); } catch (e) { /* fica só nesta sessão */ }
}
const snap = (v) => Math.max(200, Math.floor(v / 8) * 8);  // de 8 em 8: redimensionar pouco não repagina
const density = () => Math.min(3, Math.max(1, Math.round((window.devicePixelRatio || 1) * 4) / 4));
const request = (s) => ({ w: Math.min(3000, Math.max(120, s.w)), h: Math.min(3000, Math.max(120, s.h)) });

function lastRead() {
  return [...state.books].filter((b) => b.reading?.updated_at)
    .sort((a, b) => b.reading.updated_at - a.reading.updated_at)[0] || null;
}

function fontFor(id) {  // a letra com que o livro foi lido por último, ou a preferida
  const own = Number(bookById(id)?.reading?.font_size);
  if (SIZES.includes(own)) return own;
  const saved = Number(prefs().font_size);
  return SIZES.includes(saved) ? saved : 18;
}

export default {
  id: 'leitor',
  title: 'Leitor',
  icon: 'leitor',
  mount(el, ctx) {
    if (!state.books.length) {
      el.innerHTML = `<div class="empty" style="height:100%">${icon('leitor')}<div>Adicione um livro à estante para ler.</div></div>`;
      const add = h(`<button class="btn primary">${icon('plus')} Adicionar livros</button>`);
      add.addEventListener('click', () => ctx.open('estante'));
      el.querySelector('.empty').append(add);
      return () => {};
    }
    let bookId = [ctx.params.book, state.current, lastRead()?.id, state.books[0].id].find((id) => id && bookById(id));
    set('current', bookId);
    const wasExpanded = ctx.expanded();
    ctx.expand(true);  // ler pede espaço; Shift+↑ restaura a janela

    el.innerHTML = `<div class="reader">
      <header class="rd-bar">
        <button class="btn ghost small" id="rd-back" title="Voltar à estante">${icon('left')} Estante</button>
        <button class="btn ghost small" id="rd-toc-btn" aria-expanded="false" title="Sumário (T)">${icon('sumario')} Sumário</button>
        <div class="rd-title"><select id="rd-book" aria-label="Livro"></select><span class="label" id="rd-chapter"></span></div>
        <div class="rd-tools">
          <div class="rd-size" role="group" aria-label="Tamanho">
            <button class="btn ghost small" id="rd-smaller"></button><span class="label mono" id="rd-size"></span>
            <button class="btn ghost small" id="rd-bigger"></button>
          </div>
          <span id="rd-themes"></span>
          <span class="rd-spread"><span class="label">Páginas</span><span id="rd-spread"></span></span>
        </div>
      </header>
      <div class="rd-stage" id="rd-stage" tabindex="-1">
        <div class="rd-pages" id="rd-pages"></div>
        <div class="rd-msg" id="rd-msg" hidden></div>
        <nav class="rd-toc" id="rd-toc" aria-label="Sumário"></nav>
      </div>
      <footer class="rd-foot">
        <input type="range" id="rd-progress" min="1" max="1" value="1" aria-label="Posição no livro">
        <span class="label mono" id="rd-where"></span>
      </footer>
    </div>`;
    const $ = (s) => el.querySelector(s);
    const root = $('.reader');
    const stage = $('#rd-stage');
    const pagesEl = $('#rd-pages');
    const msgEl = $('#rd-msg');
    const tocEl = $('#rd-toc');
    const tocBtn = $('#rd-toc-btn');
    const progress = $('#rd-progress');
    const where = $('#rd-where');
    const bookSel = $('#rd-book');

    let info = null;   // da API: kind, pages, rtl, ratios, toc, page; mais a paginação pedida (layout)
    let spreads = [];  // páginas visíveis juntas: [[1], [2, 3], ...]
    let at = 0;        // índice do spread atual
    let geo = null;    // espaço na tela
    let zoom = 1;
    let fs = fontFor(bookId);
    let theme = THEMES.find((t) => t.id === prefs().theme) || THEMES[0];
    let spreadMode = prefs().spread;
    let token = 0;     // descarta respostas de pedidos antigos
    let saveTimer = null;
    let msgTimer = null;

    // --------------------------------------------------------- geometria
    function measure() {
      const r = stage.getBoundingClientRect();
      const W = Math.max(200, r.width - 40);
      const H = Math.max(200, r.height - 28);
      const roomy = W > H * 1.2 && W >= 640;
      return { W, H, two: zoom === 1 && (spreadMode === '2' || (spreadMode === 'auto' && roomy)) };
    }
    // livro fluido: todas as páginas têm o mesmo tamanho, com proporção de livro
    // (numa janela larga, uma página só não vira uma linha enorme)
    function pageBox(g) {
      const room = g.two ? (g.W - 2) / 2 : g.W;
      return { w: snap(Math.min(room, Math.max(340, g.H * 0.7))), h: snap(g.H) };
    }
    // livro fixo: cada página cabe no espaço com a proporção dela
    function sizeOf(p, alone) {
      if (info.kind === 'reflow') return { w: info.layout.w, h: info.layout.h };
      const ratio = info.ratios[p - 1] || 0.7;
      const room = (geo.two && !(alone && ratio > 1) ? (geo.W - 2) / 2 : geo.W) * zoom;
      const tall = geo.H * zoom;
      let w = room;
      let hh = room / ratio;
      if (hh > tall) { hh = tall; w = tall * ratio; }
      return { w: Math.max(40, Math.floor(w)), h: Math.max(40, Math.floor(hh)) };
    }
    function makeSpreads() {
      const n = info.pages;
      const out = [];
      if (!geo.two) { for (let p = 1; p <= n; p++) out.push([p]); return out; }
      if (info.kind === 'reflow') { for (let p = 1; p <= n; p += 2) out.push(p < n ? [p, p + 1] : [p]); return out; }
      const wide = (p) => (info.ratios[p - 1] || 0.7) > 1;  // página dupla: fica sozinha
      out.push([1]);  // a capa sozinha, como num livro de verdade
      for (let p = 2; p <= n;) {
        if (wide(p) || p === n || wide(p + 1)) { out.push([p]); p += 1; } else { out.push([p, p + 1]); p += 2; }
      }
      return out;
    }
    const spreadOf = (page) => Math.max(0, spreads.findIndex((s) => s.includes(page)));
    const firstPage = () => (spreads[at] || [1])[0];

    // ------------------------------------------------------------ páginas
    function urlFor(p, size) {
      return api.readerPageUrl(bookId, p, { ...request(size), fs: info.layout.fs, dpr: density(), ...theme.adj });
    }
    function hasLinks() {
      const b = bookById(bookId);
      return b && b.format !== 'comic' && b.format !== 'image';
    }
    async function addLinks(p, fig) {
      const my = token;
      let links = [];
      try {
        links = await api.readerLinks(bookId, p, { w: info.layout.w, h: info.layout.h, fs: info.layout.fs });
      } catch (e) { return; }
      if (my !== token || !fig.isConnected) return;
      for (const l of links) {
        const a = h(`<button class="rd-link" title="Ir para a página ${l.page}" aria-label="Ir para a página ${l.page}"></button>`);
        Object.assign(a.style, { left: `${l.x * 100}%`, top: `${l.y * 100}%`, width: `${l.w * 100}%`, height: `${l.h * 100}%` });
        a.addEventListener('click', (e) => { e.stopPropagation(); goPage(l.page); });
        fig.append(a);
      }
    }
    function show() {
      const spread = spreads[at] || [];
      const alone = spread.length === 1;
      const order = info.rtl ? [...spread].reverse() : spread;
      const figs = order.map((p) => {
        const size = sizeOf(p, alone);
        const fig = h(`<figure class="rd-page" data-loading style="width:${size.w}px;height:${size.h}px"><img alt="Página ${p}" draggable="false"></figure>`);
        const img = fig.querySelector('img');
        img.addEventListener('load', () => fig.removeAttribute('data-loading'), { once: true });
        img.addEventListener('error', () => { fig.removeAttribute('data-loading'); fig.setAttribute('data-broken', ''); }, { once: true });
        img.src = urlFor(p, size);
        if (hasLinks()) addLinks(p, fig);
        return fig;
      });
      pagesEl.replaceChildren(...figs);
      pagesEl.classList.toggle('two', spread.length === 2);
      if (zoom > 1) stage.scrollTo(0, 0);
      updateBars();
      prefetch();
    }
    function prefetch() {  // as próximas páginas já ficam no cache do navegador
      for (const i of [at + 1, at + 2, at - 1]) {
        const s = spreads[i];
        if (!s) continue;
        for (const p of s) { const im = new Image(); im.src = urlFor(p, sizeOf(p, s.length === 1)); }
      }
    }

    // --------------------------------------------------------- navegação
    function go(index) {
      if (!info) return;
      const i = Math.max(0, Math.min(spreads.length - 1, index));
      if (i === at && pagesEl.childElementCount) return;
      at = i;
      show();
      moved();
    }
    const next = () => go(at + 1);
    const prev = () => go(at - 1);
    const goPage = (p) => go(spreadOf(p));

    function updateBars() {
      const spread = spreads[at] || [1];
      const first = spread[0];
      const last = spread[spread.length - 1];
      progress.max = String(info.pages);
      progress.value = String(first);
      progress.disabled = info.pages < 2;
      const pct = info.pages > 1 ? Math.round(((last - 1) / (info.pages - 1)) * 100) : 100;
      where.textContent = `${first === last ? first : `${first}–${last}`} de ${info.pages} · ${pct}%`;
      let current = -1;
      info.toc.forEach((t, i) => { if (t.page <= last) current = i; });
      $('#rd-chapter').textContent = current >= 0 ? info.toc[current].title : '';
      tocEl.querySelectorAll('.rd-toc-item').forEach((b) => b.setAttribute('aria-current', String(Number(b.dataset.i) === current)));
    }
    function updateSizeCtl() {
      const fixed = info?.kind === 'fixed';
      $('#rd-smaller').textContent = fixed ? '−' : 'A−';
      $('#rd-bigger').textContent = fixed ? '+' : 'A+';
      $('#rd-smaller').title = fixed ? 'Menos zoom (−)' : 'Letra menor (−)';
      $('#rd-bigger').title = fixed ? 'Mais zoom (+)' : 'Letra maior (+)';
      $('#rd-size').textContent = fixed ? `${Math.round(zoom * 100)}%` : `Letra ${fs}`;
    }

    // ------------------------------------------------------- abrir livro
    function busy(text) {
      clearTimeout(msgTimer);
      msgTimer = setTimeout(() => { msgEl.className = 'rd-msg'; msgEl.textContent = text; msgEl.hidden = false; }, 250);
    }
    function idle() { clearTimeout(msgTimer); msgEl.hidden = true; }
    function fail(e) {
      clearTimeout(msgTimer);
      pagesEl.replaceChildren();
      msgEl.className = 'rd-msg err';
      msgEl.innerHTML = `<b>${esc(e.message || 'Não foi possível abrir o livro.')}</b>${e.hint ? `<span class="label">${esc(e.hint)}</span>` : ''}`;
      msgEl.hidden = false;
    }
    async function load() {
      const my = ++token;
      geo = measure();
      const box = pageBox(geo);
      const b = bookById(bookId);
      busy(b?.format === 'mobi' ? 'Abrindo o livro… (livros Kindle demoram um pouco na primeira vez)' : 'Abrindo o livro…');
      try {
        const res = await api.readerInfo(bookId, { w: box.w, h: box.h, fs });
        if (my !== token) return;
        info = { ...res, layout: { ...box, fs } };
        spreads = makeSpreads();
        at = spreadOf(res.page);
        idle();
        renderToc();
        updateSizeCtl();
        stage.classList.toggle('zoomed', zoom > 1);
        show();
      } catch (e) {
        if (my === token) fail(e);
      }
    }
    // outra letra ou outro tamanho de página: guarda a posição na paginação atual e
    // pede a nova; a API devolve a mesma posição (capítulo e letras antes dela)
    async function relayout() {
      await saveNow();
      await load();
    }
    function regroup() {  // mesmo livro, outro arranjo (zoom, páginas lado a lado)
      const first = firstPage();
      geo = measure();
      spreads = makeSpreads();
      at = spreadOf(first);
      show();
    }

    // ------------------------------------------------------- onde parou
    // Só a página virada pelo leitor muda a posição guardada. Abrir o livro, trocar a letra
    // ou redimensionar mostram a página que contém a posição salva, mas não a regravam: o
    // começo dessa página fica um pouco antes do ponto salvo, e a posição recuaria a cada troca.
    let dirty = false;
    function moved() {
      dirty = true;
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => saveNow(), 250);
    }
    async function saveNow(keepalive = false) {
      clearTimeout(saveTimer);
      if (!info || !dirty) return;
      dirty = false;
      const id = bookId;
      const { w, h: hh, fs: size } = info.layout;
      try {
        const reading = await api.saveReading(id, { page: firstPage(), w, h: hh, fs: size }, keepalive);
        const b = bookById(id);
        if (b) b.reading = reading;
      } catch (e) {
        dirty = true;  // a leitura continua; a posição vai na próxima vez
      }
    }

    // ------------------------------------------------------------ sumário
    function renderToc() {
      tocEl.innerHTML = `<div class="rd-toc-head"><b>Sumário</b><button class="btn ghost small" id="rd-toc-close" aria-label="Fechar o sumário">${icon('close')}</button></div>`;
      tocEl.querySelector('#rd-toc-close').addEventListener('click', closeToc);
      if (!info.toc.length) {
        tocEl.append(h('<p class="label rd-toc-empty">Este livro não tem sumário. Use a barra de baixo para ir a qualquer página.</p>'));
        return;
      }
      info.toc.forEach((t, i) => {
        const b = h(`<button class="rd-toc-item" data-i="${i}" style="--lvl:${Math.min(4, t.level - 1)}"><span>${esc(t.title)}</span><span class="label mono">${t.page}</span></button>`);
        b.addEventListener('click', () => { goPage(t.page); closeToc(); stage.focus(); });
        tocEl.append(b);
      });
    }
    function openToc() {
      root.setAttribute('data-toc', '');
      tocBtn.setAttribute('aria-expanded', 'true');
      tocEl.querySelector('[aria-current="true"]')?.scrollIntoView({ block: 'center' });
    }
    function closeToc() { root.removeAttribute('data-toc'); tocBtn.setAttribute('aria-expanded', 'false'); }
    const toggleToc = () => (root.hasAttribute('data-toc') ? closeToc() : openToc());

    // ------------------------------------------------------ letra e tema
    function step(dir) {
      if (!info) return;
      if (info.kind === 'fixed') {
        const i = ZOOMS.indexOf(zoom) + dir;
        if (i < 0 || i >= ZOOMS.length) return;
        zoom = ZOOMS[i];
        stage.classList.toggle('zoomed', zoom > 1);
        updateSizeCtl();
        regroup();
        return;
      }
      const i = SIZES.indexOf(fs) + dir;
      if (i < 0 || i >= SIZES.length) return;
      fs = SIZES[i];
      savePrefs({ font_size: fs });
      updateSizeCtl();
      relayout();
    }
    function applyTheme() {
      root.dataset.theme = theme.id;
      root.style.setProperty('--rd-paper', paperColor(theme.adj));
    }

    // ------------------------------------------------------------ eventos
    function renderBooks() {
      bookSel.innerHTML = '';
      for (const b of state.books) bookSel.append(h(`<option value="${b.id}">${esc(b.metadata?.title || b.title)}</option>`));
      bookSel.value = bookId;
    }
    bookSel.addEventListener('change', async () => {
      await saveNow();
      bookId = bookSel.value;
      set('current', bookId);
      fs = fontFor(bookId);
      zoom = 1;
      info = null;
      closeToc();
      stage.focus();
      load();
    });
    $('#rd-back').addEventListener('click', () => ctx.open('estante', { book: bookId }));
    tocBtn.addEventListener('click', toggleToc);
    $('#rd-smaller').addEventListener('click', () => step(-1));
    $('#rd-bigger').addEventListener('click', () => step(+1));
    $('#rd-themes').replaceWith(segmented(THEMES.map((t) => ({ value: t.id, label: t.label })), theme.id, (id) => {
      theme = THEMES.find((t) => t.id === id) || THEMES[0];
      savePrefs({ theme: theme.id });
      applyTheme();
      if (info) show();
    }, { label: 'Tema' }));
    $('#rd-spread').replaceWith(segmented(SPREADS, spreadMode, (v) => {
      spreadMode = v;
      savePrefs({ spread: v });
      if (!info) return;
      if (info.kind === 'reflow' && pageBox(measure()).w !== info.layout.w) relayout(); else regroup();
    }, { label: 'Páginas lado a lado' }));
    progress.addEventListener('input', () => { if (info) where.textContent = `${progress.value} de ${info.pages}`; });
    progress.addEventListener('change', () => { goPage(Number(progress.value)); stage.focus(); });

    const offKey = ctx.onKey((e) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return false;
      const k = e.key;
      if (k === 'Escape' && root.hasAttribute('data-toc')) { closeToc(); return true; }
      if (k === 't' || k === 'T') { toggleToc(); return true; }
      if (k === '+' || k === '=') { step(+1); return true; }
      if (k === '-' || k === '_') { step(-1); return true; }
      if (k === 'Home') { go(0); return true; }
      if (k === 'End') { go(spreads.length - 1); return true; }
      if (zoom > 1 && (k === 'ArrowUp' || k === 'ArrowDown')) return false;  // com zoom, rolam a página
      const forward = k === (info?.rtl ? 'ArrowLeft' : 'ArrowRight');
      const back = k === (info?.rtl ? 'ArrowRight' : 'ArrowLeft');
      if (forward || k === 'PageDown' || k === 'ArrowDown' || (k === ' ' && !e.shiftKey)) { next(); return true; }
      if (back || k === 'PageUp' || k === 'ArrowUp' || (k === ' ' && e.shiftKey)) { prev(); return true; }
      return false;
    });

    // roda do mouse e touchpad: uma página por gesto
    let wheel = 0;
    let wheelAt = 0;
    stage.addEventListener('wheel', (e) => {
      if (zoom > 1 || e.target.closest('.rd-toc')) return;  // com zoom, a roda rola a página
      e.preventDefault();
      wheel += Math.abs(e.deltaY) >= Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
      const now = performance.now();
      if (Math.abs(wheel) >= 40 && now - wheelAt > 280) {
        if (wheel > 0) next(); else prev();
        wheel = 0;
        wheelAt = now;
      }
    }, { passive: false });

    // clique nas bordas vira a página; arrastar para o lado também (tela de toque)
    let down = null;
    stage.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || e.target.closest('.rd-toc, .rd-link, .rd-msg')) { down = null; return; }
      down = { x: e.clientX, y: e.clientY };
    });
    stage.addEventListener('pointerup', (e) => {
      if (!down || !info) return;
      const dx = e.clientX - down.x;
      const dy = e.clientY - down.y;
      down = null;
      // a página à esquerda e a à direita: num mangá (da direita para a esquerda), a próxima fica à esquerda
      const toLeft = () => (info.rtl ? next() : prev());
      const toRight = () => (info.rtl ? prev() : next());
      // arrastar para a esquerda traz a página da direita, como virar a folha
      if (zoom === 1 && Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) { if (dx < 0) toRight(); else toLeft(); return; }
      if (Math.abs(dx) > 6 || Math.abs(dy) > 6) return;
      if (root.hasAttribute('data-toc')) { closeToc(); return; }
      const r = stage.getBoundingClientRect();
      const fx = (e.clientX - r.left) / r.width;
      const edge = zoom > 1 ? 0.12 : 0.3;
      if (fx < edge) toLeft(); else if (fx > 1 - edge) toRight();
    });

    const ro = new ResizeObserver(debounce(() => {
      if (!info) return;
      if (info.kind === 'reflow') {
        const box = pageBox(measure());
        if (box.w !== info.layout.w || box.h !== info.layout.h) { relayout(); return; }
      }
      regroup();
    }, 250));
    ro.observe(stage);
    const onHide = () => saveNow(true);
    window.addEventListener('pagehide', onHide);

    renderBooks();
    applyTheme();
    updateSizeCtl();
    // espera a janela terminar de expandir (0,32 s) para medir o espaço uma vez só
    setTimeout(load, wasExpanded ? 0 : 360);

    return () => {
      token += 1;  // descarta respostas a caminho
      saveNow(true);
      offKey();
      ro.disconnect();
      window.removeEventListener('pagehide', onHide);
      clearTimeout(msgTimer);
      if (!wasExpanded) ctx.expand(false);
    };
  },
};
