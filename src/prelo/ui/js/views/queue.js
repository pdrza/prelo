// Fila: cada conversão com etapa, progresso, log e o porquê de cada opção.
import { api } from '../api.js';
import { isDesktop, openFile, reveal } from '../bridge.js';
import { icon } from '../icons.js';
import { on, state } from '../store.js';
import { bytes, duration, esc, h, timeAgo } from '../ui.js';

const STATUS = { queued: 'Na fila', running: 'Convertendo', done: 'Concluído', error: 'Falhou', cancelled: 'Cancelado' };
const STAGE = { preparo: 'Preparando', entrada: 'Lendo', 'transformações': 'Ajustando', 'saída': 'Gravando', final: 'Finalizando' };
const open = new Set();

export default {
  id: 'fila',
  title: 'Fila',
  icon: 'fila',
  mount(el, ctx) {
    el.innerHTML = '<div class="pad"><h2 class="section-title">Conversões</h2><p class="lead">Cada livro é convertido num processo separado: se um arquivo travar o conversor, o resto continua funcionando.</p><div class="list" id="jobs"></div></div>';
    const list = el.querySelector('#jobs');

    function render() {
      const jobs = state.jobs;
      if (!jobs.length) {
        list.innerHTML = `<div class="empty">${icon('fila')}<div>Nenhuma conversão ainda.</div></div>`;
        return;
      }
      const keep = new Set(jobs.map((j) => j.id));
      for (const row of [...list.children]) if (!keep.has(row.dataset.id)) row.remove();
      jobs.forEach((j, idx) => {
        let row = list.querySelector(`[data-id="${j.id}"]`);
        if (!row) {
          row = h(`<div class="list-row job" data-id="${j.id}" style="grid-template-columns:44px minmax(0,1fr) auto"></div>`);
          list.append(row);
        }
        if (list.children[idx] !== row) list.insertBefore(row, list.children[idx]);
        paint(row, j);
      });
    }

    function paint(row, j) {
      const sig = `${j.status}|${Math.round(j.progress * 100)}|${j.message}|${j.log.length}|${open.has(j.id)}`;
      if (row.dataset.sig === sig) return;
      row.dataset.sig = sig;
      row.className = `list-row job ${j.status}`;
      const pct = Math.round(j.progress * 100);
      const book = state.books.find((b) => b.id === j.book_id);
      const r = j.result;
      const sub = j.status === 'running' ? `${STAGE[j.stage] || 'Iniciando'} · ${esc(j.message)} · ${pct}%`
        : j.status === 'done' ? `${r.layout === 'fixed' ? 'Layout fiel' : 'Texto fluido'} · ${bytes(r.size)} · ${duration(r.seconds)}`
          : j.status === 'error' ? esc(j.error?.message || 'Falhou') : `${STATUS[j.status]} · ${timeAgo(j.created_at)}`;
      row.innerHTML = `
        <div class="thumb" style="background-image:url('${book && book.has_cover ? api.coverUrl(book.id) : ''}')"></div>
        <div style="min-width:0">
          <div class="t">${esc(j.title)} <span class="tag ${j.status === 'done' ? 'dark' : ''}">${j.output_format.toUpperCase()}</span></div>
          <div class="s"><span>${sub}</span></div>
          ${j.status === 'running' || j.status === 'queued' ? `<div class="progress"><i style="width:${pct}%"></i></div>` : ''}
          ${j.error?.hint ? `<div class="err">${esc(j.error.hint)}</div>` : ''}
          <div class="det" ${open.has(j.id) ? '' : 'hidden'}></div>
        </div>
        <div class="row" style="flex-wrap:nowrap"></div>`;
      const actions = row.querySelector('.row');
      const btn = (label, ico, fn, cls = 'ghost small') => {
        const b = h(`<button class="btn ${cls}" title="${label}">${icon(ico)}<span class="sr">${label}</span></button>`);
        b.addEventListener('click', fn);
        actions.append(b);
      };
      if (j.status === 'running' || j.status === 'queued') btn('Cancelar', 'stop', () => api.cancelJob(j.id).then(ctx.refreshJobs));
      if (j.status === 'done') {
        if (isDesktop()) {
          btn('Abrir', 'file', () => openFile(r.output_path));
          btn('Mostrar na pasta', 'folder', () => reveal(r.output_path));
        } else btn('Baixar', 'download', () => { location.href = api.downloadUrl(j.id); });
      }
      if (j.status === 'error' || j.status === 'cancelled') {
        btn('Tentar de novo', 'retry', async () => {
          try { await api.newJob({ book_id: j.book_id, output_format: j.output_format, options: j.options }); ctx.refreshJobs(); } catch (e) { ctx.error(e); }
        });
      }
      if (!['running', 'queued'].includes(j.status)) btn('Remover da lista', 'close', () => api.deleteJob(j.id).then(ctx.refreshJobs));
      btn(open.has(j.id) ? 'Ocultar detalhes' : 'Detalhes', open.has(j.id) ? 'left' : 'right', () => {
        if (open.has(j.id)) open.delete(j.id); else open.add(j.id);
        row.dataset.sig = '';
        paint(row, j);
      });
      if (open.has(j.id)) {
        const det = row.querySelector('.det');
        const notes = [...(r?.notes || []), ...(r?.warnings || []).map((w) => `Aviso: ${w}`),
          ...(r?.option_notes || []).map((n) => n.message)];
        const opts = (r?.options || []).map((o) => `${o.label}: ${typeof o.value === 'boolean' ? (o.value ? 'sim' : 'não') : o.value} · ${o.source}${o.locked ? ' (fixo)' : ''}`);
        det.innerHTML = `
          ${r ? `<div class="label" style="margin-top:12px;word-break:break-all;user-select:text">${esc(r.output_path)}</div>` : ''}
          ${notes.length ? `<div class="notes">${notes.map((n) => `<div>${esc(n)}</div>`).join('')}</div>` : ''}
          ${opts.length ? `<div class="notes"><div class="label">Opções efetivas e de onde vieram</div>${opts.map((o) => `<div>${esc(o)}</div>`).join('')}</div>` : ''}
          <div class="log">${esc(j.log.join('\n') || 'Sem mensagens.')}</div>`;
      }
    }

    const off = on('jobs', render);
    render();
    ctx.watchJobs(true);
    return () => { off(); ctx.watchJobs(false); };
  },
};
