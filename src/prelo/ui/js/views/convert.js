// Converter: livros escolhidos + opções. Só as opções que o usuário mexeu são
// enviadas (nível HIGH "escolha atual"); o resto fica para as configurações
// salvas do livro e para as preferências, na mesma prioridade do Calibre.
import { api } from '../api.js';
import { chooseFolder, isDesktop } from '../bridge.js';
import { icon } from '../icons.js';
import { addToSelection, bookById, on, set, state } from '../store.js';
import { FORMAT_LABEL, esc, field, h, pagesLabel, segmented, toggle } from '../ui.js';
import { addBooks } from './shelf.js';

const form = { touched: new Set(), values: {}, format: null, output_dir: null };

const MODE_HELP = {
  auto: 'O Prelo analisa cada arquivo e escolhe. PDFs digitalizados, com colunas ou muitas imagens vão para layout fiel; livros de texto corrido, para texto fluido.',
  fixed: 'Cada página fica idêntica ao original, renderizada na resolução exata da tela. Nada quebra: colunas, tabelas, fórmulas e gráficos.',
  reflow: 'O texto é extraído e se adapta à tela, com tamanho de letra ajustável. Cabeçalhos, rodapés e hifenização são limpos.',
};

function setOpt(name, value) {
  form.values[name] = value;
  form.touched.add(name);
}

function val(name, fallback) {
  if (form.touched.has(name)) return form.values[name];
  const saved = state.settings.saved_defaults || {};
  if (name in saved) return saved[name];
  return fallback;
}

export default {
  id: 'converter',
  title: 'Converter',
  icon: 'converter',
  mount(el, ctx) {
    if (ctx.params.select) addToSelection(ctx.params.select);
    if (ctx.params.format) form.format = ctx.params.format;
    if (ctx.params.adjust && state.adjust) {
      for (const [k, v] of Object.entries(state.adjust)) if (k !== 'grayscale') setOpt(k, v);
      if (state.adjust.grayscale) setOpt('color_mode', 'grayscale');
    }
    if (!form.format) form.format = state.settings.default_format || 'azw3';
    if (form.output_dir === null) form.output_dir = state.settings.output_dir || '';

    el.innerHTML = `<div class="split">
      <div class="pad" id="left"></div>
      <aside class="side pad" id="right"></aside>
    </div>`;
    const left = el.querySelector('#left');
    const right = el.querySelector('#right');

    function renderLeft() {
      const books = state.selection.map(bookById).filter(Boolean);
      left.innerHTML = `
        <h2 class="section-title">Livros para converter</h2>
        <p class="lead">Escolha da estante ou adicione arquivos. Cada livro vira um trabalho na fila, convertido num processo separado.</p>
        <div class="row" style="margin-bottom:14px">
          <button class="btn" id="from-shelf">${icon('estante')} Da estante</button>
          <button class="btn" id="add-files">${icon('plus')} Adicionar arquivos</button>
          ${books.length ? '<button class="btn ghost small" id="clear">Limpar</button>' : ''}
        </div>
        <div class="list" id="list"></div>`;
      const list = left.querySelector('#list');
      if (!books.length) {
        list.append(h(`<div class="empty">${icon('converter')}<div>Nenhum livro escolhido.<br><span class="faint">Use “Da estante” ou arraste arquivos para a janela.</span></div></div>`));
      }
      for (const b of books) {
        const a = b.analysis;
        const row = h(`<div class="list-row">
          <div class="thumb" style="background-image:url('${b.has_cover ? api.coverUrl(b.id) : ''}')"></div>
          <div><div class="t">${esc(b.metadata?.title || b.title)}</div>
            <div class="s"><span>${FORMAT_LABEL[b.format]}</span><span>${pagesLabel(b)}</span>
            ${a ? `<span class="tag">${a.recommended === 'fixed' ? 'sugere layout fiel' : 'sugere texto fluido'}</span>` : ''}
            ${Object.keys(b.settings || {}).length ? '<span class="tag">ajustes salvos</span>' : ''}</div></div>
          <button class="btn ghost small" title="Tirar da lista">${icon('close')}</button></div>`);
        row.querySelector('button').addEventListener('click', () => set('selection', state.selection.filter((x) => x !== b.id)));
        list.append(row);
      }
      left.querySelector('#from-shelf').addEventListener('click', () => pickFromShelf(ctx));
      left.querySelector('#add-files').addEventListener('click', async () => {
        const added = await addBooks(ctx);
        addToSelection(added.map((b) => b.id));
      });
      const clear = left.querySelector('#clear');
      if (clear) clear.addEventListener('click', () => set('selection', []));
      renderSubmit();
    }

    function renderRight() {
      right.innerHTML = '';
      const stack = h('<div class="stack"></div>');
      right.append(stack);

      const chips = h('<div class="chips"></div>');
      for (const f of state.formats.outputs) {
        const c = h(`<button class="chip" aria-pressed="${f.id === form.format}"><b>${f.label}</b><small>${f.id === 'azw3' ? 'Kindle' : f.id === 'mobi' ? 'Kindle antigo' : f.id === 'epub' ? 'Kobo, Apple' : f.id === 'cbz' ? 'imagens' : f.id === 'pdf' ? 'ajustado' : 'só texto'}</small></button>`);
        c.addEventListener('click', () => { form.format = f.id; renderRight(); });
        chips.append(c);
      }
      const fmt = state.formats.outputs.find((o) => o.id === form.format);
      stack.append(field('Formato', chips));
      stack.append(h(`<p class="label" style="margin-top:-12px;line-height:1.4">${esc(fmt ? fmt.description : '')}</p>`));

      const sel = h('<select class="input"></select>');
      sel.append(h('<option value="auto">Automático (pelo formato)</option>'));
      const fams = {};
      for (const p of state.profiles) (fams[p.family] = fams[p.family] || []).push(p);
      for (const [fam, list] of Object.entries(fams)) {
        const g = document.createElement('optgroup');
        g.label = fam;
        for (const p of list) g.append(h(`<option value="${p.id}">${esc(p.name)}${p.width ? ` · ${p.width}×${p.height}` : ''}</option>`));
        sel.append(g);
      }
      sel.value = val('profile', state.settings.default_profile || 'auto');
      sel.addEventListener('change', () => setOpt('profile', sel.value));
      stack.append(field('Dispositivo', sel));

      const modeHelp = h(`<p class="label" style="line-height:1.4">${MODE_HELP[val('mode', 'auto')]}</p>`);
      stack.append(field('Modo', segmented([
        { value: 'auto', label: 'Automático' }, { value: 'fixed', label: 'Layout fiel' }, { value: 'reflow', label: 'Texto fluido' },
      ], val('mode', 'auto'), (v) => { setOpt('mode', v); modeHelp.innerHTML = MODE_HELP[v]; })));
      stack.append(modeHelp);

      stack.append(field('Qualidade das páginas', segmented([
        { value: 'maxima', label: 'Máxima' }, { value: 'alta', label: 'Alta' },
        { value: 'equilibrada', label: 'Equilibrada' }, { value: 'compacta', label: 'Compacta' },
      ], val('quality', 'alta'), (v) => setOpt('quality', v))));
      stack.append(field('Cores', segmented([
        { value: 'auto', label: 'Automático' }, { value: 'color', label: 'Colorido' }, { value: 'grayscale', label: 'Cinza' },
      ], val('color_mode', 'auto'), (v) => setOpt('color_mode', v))));
      stack.append(field('Margens', segmented([
        { value: 'none', label: 'Não cortar' }, { value: 'uniform', label: 'Uniforme' }, { value: 'page', label: 'Por página' },
      ], val('trim_margins', 'none'), (v) => setOpt('trim_margins', v))));

      const temp = val('temperature', 6500);
      const tempRow = h(`<div class="slider-row"><div class="label"><span>Temperatura da página</span><b>${temp} K</b></div>
        <input type="range" class="temp" min="1900" max="10000" step="100" value="${temp}"></div>`);
      const range = tempRow.querySelector('input');
      range.addEventListener('input', () => { tempRow.querySelector('b').textContent = `${range.value} K`; setOpt('temperature', Number(range.value)); });
      const editLink = h(`<button class="btn ghost small">${icon('pagina')} Abrir o editor de página</button>`);
      editLink.addEventListener('click', () => ctx.open('pagina', { book: state.selection[0] }));
      stack.append(tempRow, editLink);

      const outRow = h(`<div class="row"><span class="muted" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(form.output_dir || 'Ao lado do arquivo original')}</span></div>`);
      if (isDesktop()) {
        const choose = h(`<button class="btn small">${icon('folder')} Escolher</button>`);
        choose.addEventListener('click', async () => { const d = await chooseFolder(); if (d) { form.output_dir = d; renderRight(); } });
        outRow.append(choose);
        if (form.output_dir) {
          const reset = h('<button class="btn ghost small">Padrão</button>');
          reset.addEventListener('click', () => { form.output_dir = ''; renderRight(); });
          outRow.append(reset);
        }
      }
      stack.append(field('Pasta de saída', outRow));

      const adv = h('<details class="more"><summary>Texto fluido e Kindle</summary><div></div></details>');
      const box = adv.querySelector('div');
      const t = (name, label, help) => box.append(toggle(label, val(name, true), (v) => setOpt(name, v), help));
      t('remove_headers_footers', 'Remover cabeçalhos e rodapés', 'Linhas repetidas no topo e no pé das páginas.');
      t('dehyphenate', 'Juntar palavras hifenizadas', 'Mantém guarda-chuva e disse-lhe.');
      t('detect_chapters', 'Detectar capítulos', 'Capítulo, Parte, Prólogo… em português, inglês e espanhol.');
      t('keep_page_numbers', 'Manter números de página do original');
      box.append(field('Tabelas', segmented([
        { value: 'html', label: 'Tabela' }, { value: 'image', label: 'Imagem' }, { value: 'text', label: 'Texto' },
      ], val('tables', 'html'), (v) => setOpt('tables', v))));
      box.append(field('Aparecer no Kindle como', segmented([
        { value: 'EBOK', label: 'Livro' }, { value: 'PDOC', label: 'Documento' },
      ], val('kindle_doc_type', 'EBOK'), (v) => setOpt('kindle_doc_type', v))));
      const pages = h(`<input class="input" placeholder="todas (ex.: 1-20, 25)" value="${esc(val('page_range', ''))}">`);
      pages.addEventListener('input', () => setOpt('page_range', pages.value));
      box.append(field('Páginas', pages));
      stack.append(adv);

      const submit = h('<div id="submit" class="stack"></div>');
      stack.append(submit);
      renderSubmit();
    }

    function renderSubmit() {
      const box = right.querySelector('#submit');
      if (!box) return;
      const n = state.selection.length;
      box.innerHTML = '';
      const btn = h(`<button class="btn primary" ${n ? '' : 'disabled'}>${icon('converter')} Converter ${n || ''} ${n === 1 ? 'livro' : 'livros'} para ${form.format.toUpperCase()}</button>`);
      btn.addEventListener('click', submit);
      box.append(btn);
      if (form.touched.size) box.append(h(`<p class="label" style="line-height:1.4">${form.touched.size} opção(ões) escolhida(s) agora valem por cima das configurações salvas.</p>`));
    }

    async function submit() {
      const options = {};
      for (const k of form.touched) options[k] = form.values[k];
      let ok = 0;
      for (const id of state.selection) {
        try {
          await api.newJob({ book_id: id, output_format: form.format, options, output_dir: form.output_dir || undefined });
          ok += 1;
        } catch (e) { ctx.error(e); return; }
      }
      ctx.toast(`${ok} conversão(ões) na fila`, { action: { label: 'Ver fila', fn: () => ctx.open('fila') } });
      ctx.refreshJobs();
      ctx.open('fila');
    }

    const off = on('selection', renderLeft);
    const off2 = on('books', renderLeft);
    renderLeft();
    renderRight();
    return () => { off(); off2(); };
  },
};

function pickFromShelf(ctx) {
  const chosen = new Set(state.selection);
  const body = h('<div class="pad"><div class="list"></div></div>');
  const list = body.querySelector('.list');
  if (!state.books.length) list.append(h('<div class="empty">A estante está vazia.</div>'));
  for (const b of state.books) {
    const row = h(`<label class="list-row" style="cursor:pointer">
      <div class="thumb" style="background-image:url('${b.has_cover ? api.coverUrl(b.id) : ''}')"></div>
      <div><div class="t">${esc(b.metadata?.title || b.title)}</div><div class="s">${[FORMAT_LABEL[b.format], pagesLabel(b)].filter(Boolean).join(' · ')}</div></div>
      <span class="check"><input type="checkbox" ${chosen.has(b.id) ? 'checked' : ''}></span></label>`);
    row.querySelector('input').addEventListener('change', (e) => { if (e.target.checked) chosen.add(b.id); else chosen.delete(b.id); });
    list.append(row);
  }
  ctx.modal('Escolher da estante', body, [{ label: 'Usar selecionados', primary: true, fn: () => set('selection', [...chosen]) }]);
}
