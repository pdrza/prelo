// Janelas menores: Dispositivos, API, Preferências e Sobre.
import { api } from '../api.js';
import { chooseFolder, isDesktop } from '../bridge.js';
import { icon } from '../icons.js';
import { set, state } from '../store.js';
import { esc, field, h, segmented, toggle } from '../ui.js';

async function saveSettings(ctx, patch, message) {
  try {
    set('settings', await api.saveSettings(patch));
    ctx.applySettings();
    if (message) ctx.toast(message);
  } catch (e) { ctx.error(e); }
}

export const devices = {
  id: 'dispositivos',
  title: 'Dispositivos',
  icon: 'dispositivos',
  mount(el, ctx) {
    function render() {
      const current = state.settings.default_profile || 'auto';
      el.innerHTML = `<div class="pad"><h2 class="section-title">Dispositivos</h2>
        <p class="lead">No layout fiel, cada página é renderizada na resolução exata da tela escolhida: nitidez máxima sem arquivo inflado. Telas e-ink monocromáticas recebem tons de cinza automaticamente (você pode trocar). Clique para usar como padrão.</p>
        <div class="grid-cards" id="cards"></div></div>`;
      const cards = el.querySelector('#cards');
      const auto = h(`<button class="card" aria-pressed="${current === 'auto'}"><div class="screen"><i style="--ar:.75;border-style:dashed"></i></div>
        <div><div class="name">Automático</div><div class="spec"><span>Kindle para AZW3/MOBI, tablet para EPUB</span></div></div></button>`);
      auto.addEventListener('click', () => saveSettings(ctx, { default_profile: 'auto' }, 'Dispositivo padrão: automático').then(render));
      cards.append(auto);
      for (const p of state.profiles) {
        const ar = p.width ? (p.width / p.height).toFixed(3) : '0.72';
        const c = h(`<button class="card" aria-pressed="${current === p.id}">
          <div class="screen"><i class="${p.color ? 'color' : ''}" style="--ar:${ar}"></i></div>
          <div><div class="name">${esc(p.name)}</div>
          <div class="spec"><span class="mono">${p.width ? `${p.width}×${p.height}` : 'tamanho original'}</span><span>${p.eink ? 'e-ink' : 'tela'} ${p.color ? 'colorida' : 'cinza'}</span><span>${p.preferred_format.toUpperCase()}</span></div></div></button>`);
        c.addEventListener('click', () => saveSettings(ctx, { default_profile: p.id }, `Dispositivo padrão: ${p.name}`).then(render));
        cards.append(c);
      }
    }
    render();
    return () => {};
  },
};

const ENDPOINTS = [
  ['GET', '/api/health', 'Estado da API, versão e motores disponíveis'],
  ['GET', '/api/formats', 'Formatos de entrada e saída'],
  ['GET', '/api/profiles', 'Dispositivos e resoluções'],
  ['GET', '/api/options', 'Todas as opções de conversão, com padrão e ajuda'],
  ['GET', '/api/books', 'Livros da estante'],
  ['POST', '/api/books', 'Adiciona arquivos locais (só no app)'],
  ['POST', '/api/books/upload', 'Envia arquivos (multipart)'],
  ['PATCH', '/api/books/{id}', 'Salva metadados e configurações do livro'],
  ['GET', '/api/books/{id}/pages/{n}/preview', 'Página renderizada com ajustes (JPEG)'],
  ['GET', '/api/books/{id}/reader', 'Leitor: páginas, sumário e onde parou'],
  ['GET', '/api/books/{id}/reader/pages/{n}', 'Página para ler (JPEG), com letra e tema'],
  ['PUT', '/api/books/{id}/reader/position', 'Guarda onde o leitor parou'],
  ['POST', '/api/jobs', 'Cria uma conversão'],
  ['GET', '/api/jobs/{id}', 'Progresso, log e resultado'],
  ['POST', '/api/jobs/{id}/cancel', 'Cancela'],
  ['GET', '/api/jobs/{id}/download', 'Baixa o arquivo convertido'],
  ['GET/PUT', '/api/settings', 'Preferências'],
];

export const apiView = {
  id: 'api',
  title: 'API',
  icon: 'api',
  mount(el, ctx) {
    const base = `${location.protocol}//${location.host}`;
    const hlth = state.health || {};
    el.innerHTML = `<div class="pad"><div class="stack" style="max-width:860px">
      <div><h2 class="section-title">API local</h2>
      <p class="lead">Esta janela é só um cliente da API. Scripts, outros apps e a linha de comando usam a mesma API. Ela escuta apenas em 127.0.0.1 e exige o token desta sessão.</p></div>
      <div class="row"><span class="tag dark">online</span><span class="tag mono">${esc(base)}</span><span class="tag">modo ${esc(hlth.mode || '')}</span>
        <span class="tag">versão ${esc(hlth.version || '')}</span><span class="tag">Calibre ${hlth.calibre ? 'encontrado' : 'não instalado'}</span></div>
      <div class="row"><a class="btn" href="/api/docs" target="_blank" rel="noopener">${icon('external')} Documentação interativa</a></div>
      <table class="endpoints"><tbody>${ENDPOINTS.map(([m, p, d]) => `<tr><td><span class="method">${m}</span></td><td><code>${esc(p)}</code></td><td class="muted">${esc(d)}</td></tr>`).join('')}</tbody></table>
      <div><div class="label" style="margin-bottom:8px">Exemplo: converter um livro da estante para AZW3 com página quente</div>
<pre class="code">curl -X POST ${esc(base)}/api/jobs \\
  -H "X-Prelo-Token: SEU_TOKEN" -H "Content-Type: application/json" \\
  -d '{"book_id": "ID_DO_LIVRO", "output_format": "azw3",
       "options": {"profile": "kindle-pw5", "mode": "fixed", "temperature": 4800}}'</pre></div>
      <div><div class="label" style="margin-bottom:8px">Sem interface: linha de comando</div>
<pre class="code">prelo convert livro.pdf -f azw3 --profile kindle-pw5 --temperature 4800
prelo serve --port 8765        # API para outros programas</pre></div>
    </div></div>`;
    return () => {};
  },
};

export const settingsView = {
  id: 'preferencias',
  title: 'Preferências',
  icon: 'preferencias',
  mount(el, ctx) {
    function render() {
      const s = state.settings;
      el.innerHTML = '<div class="pad"><div class="stack" id="f" style="max-width:620px"><h2 class="section-title">Preferências</h2></div></div>';
      const f = el.querySelector('#f');
      const out = h(`<div class="row"><span class="muted" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.output_dir || 'Ao lado do arquivo original')}</span></div>`);
      if (isDesktop()) {
        const c = h(`<button class="btn small">${icon('folder')} Escolher</button>`);
        c.addEventListener('click', async () => { const d = await chooseFolder(); if (d) saveSettings(ctx, { output_dir: d }).then(render); });
        out.append(c);
        if (s.output_dir) {
          const r = h('<button class="btn ghost small">Ao lado do original</button>');
          r.addEventListener('click', () => saveSettings(ctx, { output_dir: '' }).then(render));
          out.append(r);
        }
      }
      f.append(field('Pasta de saída', out));
      f.append(h('<p class="label" style="line-height:1.4;margin-top:-12px">O Prelo nunca sobrescreve um arquivo: se o nome existir, o novo ganha “(2)”.</p>'));
      f.append(field('Formato padrão', segmented(state.formats.outputs.map((o) => ({ value: o.id, label: o.label })),
        s.default_format, (v) => saveSettings(ctx, { default_format: v }))));
      f.append(field('Tema', segmented([{ value: 'system', label: 'Sistema' }, { value: 'light', label: 'Claro' }, { value: 'dark', label: 'Escuro' }],
        s.theme, (v) => saveSettings(ctx, { theme: v }))));
      f.append(field('Conversões ao mesmo tempo', segmented([1, 2, 3, 4].map((n) => ({ value: n, label: String(n) })),
        s.parallel_jobs, (v) => saveSettings(ctx, { parallel_jobs: v }))));
      f.append(toggle('Modo desempenho', s.reduce_motion, (v) => saveSettings(ctx, { reduce_motion: v }), 'Desliga as animações da janela e do carrossel.'));
      if (isDesktop()) f.append(toggle('Abrir a pasta ao concluir', s.open_folder_when_done, (v) => saveSettings(ctx, { open_folder_when_done: v })));
      const sd = s.saved_defaults || {};
      const keys = Object.keys(sd);
      const box = h(`<div class="field"><div class="label">Opções padrão salvas</div><div class="muted">${keys.length ? keys.map((k) => `${esc(k)} = ${esc(sd[k])}`).join(' · ') : 'Nenhuma. Use “Usar como padrão” no editor de página.'}</div></div>`);
      if (keys.length) {
        const clr = h('<button class="btn small" style="justify-self:start">Limpar</button>');
        clr.addEventListener('click', () => saveSettings(ctx, { saved_defaults: {} }, 'Padrões limpos').then(render));
        box.append(clr);
      }
      f.append(box);
      const engine = segmented([{ value: 'native', label: 'Nativo' }, { value: 'calibre', label: 'Calibre' }],
        sd.kindle_engine || 'native', (v) => saveSettings(ctx, { saved_defaults: { ...sd, kindle_engine: v } }).then(render));
      f.append(field('Motor AZW3/MOBI', engine, state.health?.calibre ? 'Calibre encontrado' : 'Calibre não instalado'));
    }
    render();
    return () => {};
  },
};

export const about = {
  id: 'sobre',
  title: 'Sobre',
  icon: 'sobre',
  mount(el) {
    el.innerHTML = `<div class="pad"><div class="stack" style="max-width:720px">
      <div><h2 class="section-title">Prelo ${esc(state.health?.version || '')}</h2>
      <p class="lead">Conversor universal de livros. PDF, EPUB, livros Kindle (AZW3/MOBI), quadrinhos e imagens para EPUB, AZW3, MOBI, CBZ, PDF e TXT, mantendo o layout quando é isso que importa e limpando o texto quando ele precisa fluir.</p></div>
      <div><div class="label" style="margin-bottom:10px">Regras herdadas do Calibre</div>
      <div class="notes" style="margin:0">
        <div>· Tudo passa por um livro intermediário: qualquer entrada combina com qualquer saída.</div>
        <div>· Prioridade de opções LOW/MED/HIGH; recomendação HIGH de plugin trava a opção.</div>
        <div>· Conversão em processo separado, sobre cópias, com gravação atômica; saída vazia é erro.</div>
        <div>· Livros com DRM são recusados; ZIPs com caminhos perigosos também.</div>
        <div>· Tamanho base = o que cobre mais caracteres; sumário original preservado se o gerado ficar pobre.</div>
      </div></div>
      <div><div class="label" style="margin-bottom:10px">O que o Prelo faz diferente</div>
      <div class="notes" style="margin:0">
        <div>· Layout fiel para PDF (o Calibre não suporta colunas, tabelas nem gráficos vetoriais em PDF).</div>
        <div>· Formato detectado pelo conteúdo, não pela extensão; nunca sobrescreve arquivos.</div>
        <div>· Capítulos em português; hifenização que respeita guarda-chuva e disse-lhe.</div>
        <div>· Opção com nome errado é erro, não é ignorada em silêncio.</div>
        <div>· AZW3 gerado sem Calibre nem KindleGen; temperatura da página no próprio arquivo.</div>
      </div></div>
      <p class="label" style="line-height:1.5">Licença AGPL-3.0. Usa PyMuPDF (AGPL), Pillow, lxml, FastAPI, pywebview, Three.js (MIT) e a fonte Inter (OFL). Mecânica da janela inspirada no site do PostHog; paleta e carrossel inspirados no site do unveil.</p>
    </div></div>`;
    return () => {};
  },
};
