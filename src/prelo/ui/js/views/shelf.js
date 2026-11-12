// Estante: o carrossel 3D com as capas dos livros.
import { api } from '../api.js';
import { chooseBooks, isDesktop, reveal } from '../bridge.js';
import { icon } from '../icons.js';
import { Shelf } from '../shelf3d.js';
import { bookById, on, refreshBooks, set, state } from '../store.js';
import { FORMAT_LABEL, bytes, esc, h, pagesLabel } from '../ui.js';

const QUICK = ['epub', 'azw3', 'mobi', 'cbz', 'pdf'];
const MODE = { fixed: 'Layout fiel', reflow: 'Texto fluido' };

export async function addBooks(ctx) {
  try {
    const res = await chooseBooks();
    await refreshBooks();
    if (res.added.length) ctx.toast(`${res.added.length} livro(s) na estante`, { sub: res.added.map((b) => b.title).slice(0, 3).join(' · ') });
    for (const e of res.errors) ctx.toast(e.message, { kind: 'err', sub: e.path });
    return res.added;
  } catch (e) {
    ctx.error(e);
    return [];
  }
}

export default {
  id: 'estante',
  title: 'Estante',
  icon: 'estante',
  mount(el, ctx) {
    el.innerHTML = `
      <div class="shelf">
        <span class="hint">Role, arraste ou use as setas · clique numa capa</span>
        <span class="float-label"></span>
        <div class="edge"></div>
        <div class="book-detail" id="detail" aria-live="polite"></div>
        <div class="bar">
          <div class="row">
            <button class="btn primary" id="add">${icon('plus')} Adicionar livros</button>
            <span class="label" id="count"></span>
          </div>
          <span class="label">Arraste arquivos para qualquer lugar da janela</span>
        </div>
      </div>`;
    const root = el.querySelector('.shelf');
    const detail = el.querySelector('#detail');
    let quickFormat = state.settings.default_format || 'azw3';
    let lastIds = null;  // null: a estante vazia também precisa desenhar as placas de exemplo

    const hint = el.querySelector('.hint');
    const shelf = new Shelf(root, {
      onOpen: (item) => {
        root.classList.add('open');
        hint.textContent = 'Clique fora da capa ou aperte Esc para voltar';
        showDetail(item.id);
      },
      onClose: () => {
        root.classList.remove('open');
        hint.textContent = 'Role, arraste ou use as setas · clique numa capa';
        detail.classList.remove('show');
      },
      onClickEmpty: () => addBooks(ctx),
    });

    function items() {
      return state.books.map((b) => ({
        id: b.id,
        title: b.metadata?.title || b.title,
        subtitle: (b.metadata?.authors ? [b.metadata.authors].flat().join(', ') : (b.authors || []).join(', ')) || FORMAT_LABEL[b.format],
        cover: b.has_cover ? `${api.coverUrl(b.id)}?v=${Math.round(b.added_at)}` : null,
        ratio: b.cover_ratio || 1.5,
      }));
    }

    function render() {
      const ids = state.books.map((b) => b.id).join();
      el.querySelector('#count').textContent = state.books.length
        ? `${state.books.length} ${state.books.length === 1 ? 'livro' : 'livros'}` : 'Estante vazia';
      if (ids === lastIds) return;
      lastIds = ids;
      detail.classList.remove('show');
      shelf.setItems(items());
    }

    function showDetail(id) {
      const b = bookById(id);
      if (!b) return;
      set('current', id);
      const a = b.analysis;
      const why = a ? `Recomendado: <span class="tag dark">${MODE[a.recommended]}</span> ${esc(a.reasons[0] || '')}` : '';
      const authors = (b.authors || []).join(', ');
      const lido = b.reading?.fraction || 0;
      const read = lido >= 0.01 ? `Continuar (${Math.round(lido * 100)}%)` : 'Ler';
      detail.innerHTML = `
        <span class="label">${[FORMAT_LABEL[b.format], pagesLabel(b, true), bytes(b.size)].filter(Boolean).join(' · ')}</span>
        <div><h2>${esc(b.metadata?.title || b.title)}</h2>${authors ? `<div class="muted" style="margin-top:6px">${esc(authors)}</div>` : ''}</div>
        ${why ? `<div class="why">${why}</div>` : ''}
        <div class="chips" id="quick"></div>
        <div class="row">
          <button class="btn primary" id="go">${icon('converter')} Converter</button>
          <button class="btn" id="read">${icon('leitor')} ${read}</button>
          <button class="btn" id="more">Mais opções</button>
        </div>
        <div class="row">
          <button class="btn ghost small" data-a="page">${icon('pagina')} Ajustar páginas</button>
          <button class="btn ghost small" data-a="meta">${icon('metadados')} Metadados</button>
          ${isDesktop() ? `<button class="btn ghost small" data-a="reveal">${icon('folder')} Mostrar arquivo</button>` : ''}
          <button class="btn ghost small danger" data-a="remove">${icon('trash')} Remover</button>
        </div>
        ${b.exists === false ? '<div class="label" style="color:var(--danger)">O arquivo original não foi encontrado no caminho salvo.</div>' : ''}`;
      const quick = detail.querySelector('#quick');
      for (const f of QUICK) {
        const out = state.formats.outputs.find((o) => o.id === f);
        if (!out) continue;
        const btn = h(`<button class="chip" aria-pressed="${f === quickFormat}"><b>${out.label}</b><small>${f === 'azw3' || f === 'mobi' ? 'Kindle' : f === 'epub' ? 'Kobo, Apple' : f === 'cbz' ? 'imagens' : 'ajustado'}</small></button>`);
        btn.addEventListener('click', () => {
          quickFormat = f;
          quick.querySelectorAll('.chip').forEach((c) => c.setAttribute('aria-pressed', String(c === btn)));
        });
        quick.append(btn);
      }
      detail.querySelector('#go').addEventListener('click', async () => {
        try {
          await api.newJob({ book_id: id, output_format: quickFormat, options: {} });
          ctx.toast(`Convertendo para ${quickFormat.toUpperCase()}`, { sub: b.title, action: { label: 'Ver fila', fn: () => ctx.open('fila') } });
          ctx.refreshJobs();
        } catch (e) { ctx.error(e); }
      });
      detail.querySelector('#more').addEventListener('click', () => ctx.open('converter', { select: [id], format: quickFormat }));
      detail.querySelector('#read').addEventListener('click', () => ctx.open('leitor', { book: id }));
      detail.querySelectorAll('[data-a]').forEach((btn) => btn.addEventListener('click', async () => {
        const act = btn.dataset.a;
        if (act === 'page') ctx.open('pagina', { book: id });
        if (act === 'meta') ctx.open('metadados', { book: id });
        if (act === 'reveal') reveal(b.path);
        if (act === 'remove') {
          try {
            await api.deleteBook(id);
          } catch (e) { ctx.error(e); return; }
          shelf.close();
          await refreshBooks();
          ctx.toast('Removido da estante', { sub: 'O arquivo original não foi apagado.' });
        }
      }));
      requestAnimationFrame(() => detail.classList.add('show'));
    }

    el.querySelector('#add').addEventListener('click', () => addBooks(ctx));
    const offBooks = on('books', render);
    const offKey = ctx.onKey((e) => shelf.key(e));
    const offTheme = ctx.onTheme(() => shelf.setBackground());
    render();
    if (ctx.params.book) setTimeout(() => shelf.openById(ctx.params.book), 400);

    return () => { offBooks(); offKey(); offTheme(); shelf.destroy(); };
  },
};
