// Estado compartilhado entre as janelas, com assinaturas simples.
import { api } from './api.js';

export const state = {
  health: null,
  books: [],
  jobs: [],
  settings: {},
  profiles: [],
  formats: { inputs: {}, outputs: [] },
  options: [],
  selection: [],        // livros escolhidos para converter
  current: null,        // livro em foco (editor de página, metadados)
  adjust: null,         // ajustes de página em edição
};

const subs = new Map();

export function on(key, fn) {
  if (!subs.has(key)) subs.set(key, new Set());
  subs.get(key).add(fn);
  return () => subs.get(key).delete(fn);
}

export function set(key, value) {
  state[key] = value;
  (subs.get(key) || []).forEach((fn) => fn(value));
}

export const bookById = (id) => state.books.find((b) => b.id === id) || null;
export const profileById = (id) => state.profiles.find((p) => p.id === id) || null;
export const optionSpec = (name) => state.options.find((o) => o.name === name) || null;

export async function refreshBooks() {
  set('books', await api.books());
  set('selection', state.selection.filter((id) => bookById(id)));
  if (state.current && !bookById(state.current)) set('current', null);
  return state.books;
}

export async function refreshJobs() {
  set('jobs', await api.jobs());
  return state.jobs;
}

export async function loadAll() {
  const [health, formats, profiles, options, settings] = await Promise.all([
    api.health(), api.formats(), api.profiles(), api.options(), api.settings(),
  ]);
  Object.assign(state, { health, formats, profiles, options, settings });
  await Promise.all([refreshBooks(), refreshJobs()]);
}

export function defaultAdjust() {
  return { temperature: 6500, brightness: 0, contrast: 0, gamma: 1.0, saturation: 0,
           invert: false, sharpen: false, normalize: false, grayscale: false };
}

export function addToSelection(ids) {
  const next = [...state.selection];
  for (const id of ids) if (!next.includes(id)) next.push(id);
  set('selection', next);
}
