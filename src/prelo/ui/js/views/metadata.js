// Metadados: o que vai no livro convertido (título, autores, série...).
import { api } from '../api.js';
import { icon } from '../icons.js';
import { bookById, set, state } from '../store.js';
import { FORMAT_LABEL, esc, h, pagesLabel } from '../ui.js';

const FIELDS = [
  { k: 'title', label: 'Título' },
  { k: 'authors', label: 'Autores', help: 'separados por vírgula' },
  { k: 'language', label: 'Idioma', help: 'ex.: pt-BR, en, es' },
  { k: 'publisher', label: 'Editora' },
  { k: 'date', label: 'Data', help: 'AAAA ou AAAA-MM-DD' },
  { k: 'isbn', label: 'ISBN' },
  { k: 'series', label: 'Série' },
  { k: 'series_index', label: 'Número na série' },
  { k: 'subjects', label: 'Assuntos', help: 'separados por vírgula' },
];

export default {
  id: 'metadados',
  title: 'Metadados',
  icon: 'metadados',
  mount(el, ctx) {
    if (ctx.params.book) set('current', ctx.params.book);
    let id = state.current || (state.books[0] && state.books[0].id);
    if (!id) {
      el.innerHTML = `<div class="empty" style="height:100%">${icon('metadados')}<div>A estante está vazia.</div></div>`;
      return () => {};
    }

    function render() {
      const b = bookById(id);
      const md = b.metadata || {};
      const original = { title: b.title, authors: (b.authors || []).join(', '), language: b.language };
      el.innerHTML = `<div class="split"><div class="pad"><div class="stack" id="form" style="max-width:640px"></div></div>
        <aside class="side pad" id="side"></aside></div>`;
      const form = el.querySelector('#form');
      const sel = h('<select class="input"></select>');
      for (const x of state.books) sel.append(h(`<option value="${x.id}">${esc(x.metadata?.title || x.title)}</option>`));
      sel.value = id;
      sel.addEventListener('change', () => { id = sel.value; set('current', id); render(); });
      form.append(h('<h2 class="section-title">Metadados</h2>'),
        h('<p class="lead" style="margin:0">Campos vazios mantêm o que veio do arquivo. Os valores ficam salvos para este livro e entram em todas as conversões dele.</p>'),
        sel);
      const grid = h('<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px"></div>');
      const inputs = {};
      for (const f of FIELDS) {
        const v = md[f.k] ?? '';
        const shown = Array.isArray(v) ? v.join(', ') : v;
        const ph = original[f.k] || '';
        const wrap = h(`<label class="field"><span class="label">${f.label}${f.help ? ` <span class="faint">· ${f.help}</span>` : ''}</span>
          <input class="input" value="${esc(shown)}" placeholder="${esc(ph)}"></label>`);
        inputs[f.k] = wrap.querySelector('input');
        grid.append(wrap);
      }
      form.append(grid);
      const desc = h(`<label class="field"><span class="label">Descrição</span><textarea class="input">${esc(md.description || '')}</textarea></label>`);
      form.append(desc);
      const save = h(`<button class="btn primary" style="justify-self:start">${icon('check')} Salvar metadados</button>`);
      save.addEventListener('click', async () => {
        const out = {};
        for (const f of FIELDS) {
          const v = inputs[f.k].value.trim();
          if (v) out[f.k] = v;
        }
        const d = desc.querySelector('textarea').value.trim();
        if (d) out.description = d;
        try {
          await api.patchBook(id, { metadata: out });
          await ctx.refreshBooks();
          ctx.toast('Metadados salvos', { sub: out.title || b.title });
        } catch (e) { ctx.error(e); }
      });
      form.append(save);

      el.querySelector('#side').innerHTML = `
        <div class="stack">
          <div class="thumb" style="width:150px;background-image:url('${b.has_cover ? api.coverUrl(b.id) : ''}')"></div>
          <div><div class="label">Arquivo</div><div style="word-break:break-all;margin-top:6px;user-select:text">${esc(b.name)}</div></div>
          <div class="row"><span class="tag">${FORMAT_LABEL[b.format]}</span>${b.pages ? `<span class="tag">${pagesLabel(b)}</span>` : ''}${b.language ? `<span class="tag">${esc(b.language)}</span>` : ''}</div>
          <p class="label" style="line-height:1.4">A capa vem da primeira página (PDF) ou da capa do EPUB ou do livro Kindle. Para usar outra página como capa, escolha “Página da capa” nas opções avançadas.</p>
        </div>`;
    }
    render();
    return () => {};
  },
};
