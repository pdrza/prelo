// Componentes pequenos reutilizados pelas janelas.

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function h(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

export function bytes(n) {
  if (!n && n !== 0) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1).replace('.', ',')} MB`;
}

export function duration(s) {
  if (s == null) return '';
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0).replace('.', ',')} s`;
  return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

/** Segmentado: [{value, label}] → elemento; onChange(value). */
export function segmented(items, value, onChange, { label } = {}) {
  const el = h(`<div class="seg" role="group"${label ? ` aria-label="${esc(label)}"` : ''}></div>`);
  for (const it of items) {
    const b = h(`<button type="button" aria-pressed="${it.value === value}">${esc(it.label)}</button>`);
    b.addEventListener('click', () => {
      el.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      onChange(it.value);
    });
    el.append(b);
  }
  return el;
}

export function field(label, control, aside = '') {
  const el = h(`<div class="field"><div class="label"><span>${esc(label)}</span><span>${aside}</span></div></div>`);
  el.append(control);
  return el;
}

export function toggle(label, checked, onChange, help = '') {
  const el = h(`<label class="check"><input type="checkbox" ${checked ? 'checked' : ''}><span>${esc(label)}${help ? `<small>${esc(help)}</small>` : ''}</span></label>`);
  el.querySelector('input').addEventListener('change', (e) => onChange(e.target.checked));
  return el;
}

// mesma conversão Kelvin → RGB do motor (core/imaging.py)
function kelvin(k) {
  const t = k / 100;
  let r; let g; let b;
  if (t <= 66) { r = 255; g = 99.4708025861 * Math.log(t) - 161.1195681661; b = t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307; } else {
    r = 329.698727446 * Math.pow(t - 60, -0.1332047592); g = 288.1221695283 * Math.pow(t - 60, -0.0755148492); b = 255;
  }
  return [r, g, b].map((v) => Math.max(0, Math.min(255, v)));
}

/** Cor que o papel branco ganha com os ajustes de página (temperatura, modo noturno). */
export function paperColor(adj) {
  if (adj.grayscale) return '#e9e9e9';
  const ref = kelvin(6500);
  const cur = kelvin(adj.temperature ?? 6500);
  const f = cur.map((c, i) => c / ref[i]);
  const top = Math.max(...f);
  let rgb = f.map((x) => Math.round((x / top) * 255));
  if (adj.invert) rgb = rgb.map((x) => Math.round(x * 0.12));
  return `rgb(${rgb.join(',')})`;
}

export function debounce(fn, ms = 150) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

export function timeAgo(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return 'agora';
  if (s < 3600) return `há ${Math.floor(s / 60)} min`;
  if (s < 86400) return `há ${Math.floor(s / 3600)} h`;
  return new Date(ts * 1000).toLocaleDateString('pt-BR');
}

export const FORMAT_LABEL = { pdf: 'PDF', epub: 'EPUB', comic: 'CBZ', image: 'Imagem', mobi: 'Kindle' };

/** "12 pág." ou "8 cap." (EPUB e Kindle contam capítulos); vazio quando não se sabe. */
export function pagesLabel(b, long = false) {
  if (!b.pages) return '';
  const chapters = b.format === 'epub' || b.format === 'mobi';
  if (long) return `${b.pages} ${chapters ? (b.pages === 1 ? 'capítulo' : 'capítulos') : (b.pages === 1 ? 'página' : 'páginas')}`;
  return `${b.pages} ${chapters ? 'cap.' : 'pág.'}`;
}
