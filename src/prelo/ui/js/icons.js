// Ícones de traço fino (24×24), no espírito monocromático do unveil.
const P = {
  logo: '<rect x="2" y="2" width="20" height="20" rx="6" fill="currentColor" stroke="none"/><g transform="rotate(-30 12 12)" stroke="none"><rect x="7.2" y="5.6" width="6.4" height="10.4" rx=".9" fill="var(--frame)" opacity=".5"/><rect x="10" y="7.4" width="6.4" height="10.4" rx=".9" fill="var(--frame)"/></g>',
  estante: '<path d="M4 4.5h3.2v15H4zM9.2 4.5h3.2v15H9.2z"/><path d="m15 5.6 3.1-.8 3.6 14-3.1.8z"/><path d="M2.5 19.5h19"/>',
  leitor: '<path d="M12 6.8C10.2 5.5 7.7 4.8 4 4.8v13.4c3.7 0 6.2.7 8 2 1.8-1.3 4.3-2 8-2V4.8c-3.7 0-6.2.7-8 2z"/><path d="M12 6.8v13.4"/>',
  sumario: '<path d="M9 6.5h11M9 12h11M9 17.5h11M4.2 6.5h1.6M4.2 12h1.6M4.2 17.5h1.6"/>',
  converter: '<path d="M4 8.5h14.5l-3.5-3.5"/><path d="M20 15.5H5.5L9 19"/>',
  pagina: '<rect x="5" y="3" width="14" height="18" rx="1.5"/><path d="M8.5 7.5h7M8.5 11h7M8.5 14.5h4"/><circle cx="17.5" cy="17.5" r="3.5" fill="var(--surface)"/><path d="M17.5 15.6v1.9l1.2.8"/>',
  metadados: '<path d="M3.5 12.2V4.5a1 1 0 0 1 1-1h7.7l8.3 8.3-8.7 8.7z"/><circle cx="8" cy="8" r="1.4"/>',
  fila: '<path d="M4 6.5h16M4 12h11M4 17.5h7"/><circle cx="18" cy="17.5" r="3"/><path d="M18 16.2v1.5l.9.6"/>',
  dispositivos: '<rect x="5.5" y="2.5" width="13" height="19" rx="2"/><path d="M10 18.5h4M8.5 6h7M8.5 9h7M8.5 12h5"/>',
  api: '<path d="M8.5 4.5c-2 0-3 1-3 3v2c0 1.2-.8 2.5-2.2 2.5 1.4 0 2.2 1.3 2.2 2.5v2c0 2 1 3 3 3"/><path d="M15.5 4.5c2 0 3 1 3 3v2c0 1.2.8 2.5 2.2 2.5-1.4 0-2.2 1.3-2.2 2.5v2c0 2-1 3-3 3"/>',
  preferencias: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  sobre: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.2M12 7.8v.2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  expand: '<path d="M14 4h6v6M10 20H4v-6M20 4l-6.5 6.5M4 20l6.5-6.5"/>',
  collapse: '<path d="M20 10h-6V4M4 14h6v6M14 10l6.5-6.5M10 14l-6.5 6.5"/>',
  left: '<path d="m14.5 6-6 6 6 6"/>',
  right: '<path d="m9.5 6 6 6-6 6"/>',
  folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
  download: '<path d="M12 4v11M7 10.5l5 5 5-5M5 19.5h14"/>',
  trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5h9l1-12.5"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6"/>',
  moon: '<path d="M19.5 14.5A7.5 7.5 0 0 1 9.5 4.5a7.5 7.5 0 1 0 10 10z"/>',
  external: '<path d="M13.5 4.5h6v6M19.5 4.5l-8 8M18 13.5v5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6h5"/>',
  file: '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4"/>',
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="1.5"/>',
  retry: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4.2h-4.2"/>',
};

export function icon(name, cls = 'i') {
  const body = P[name] || P.file;
  return `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;
}
