// Ponte com o app nativo (pywebview). No navegador comum, cai para upload.
import { api } from './api.js';

let readyPromise = null;

export function desktopReady() {
  if (window.pywebview && window.pywebview.api) return Promise.resolve(true);
  if (!readyPromise) {
    readyPromise = new Promise((resolve) => {
      window.addEventListener('pywebviewready', () => resolve(true), { once: true });
      setTimeout(() => resolve(Boolean(window.pywebview && window.pywebview.api)), 1500);
    });
  }
  return readyPromise;
}

export const isDesktop = () => Boolean(window.pywebview && window.pywebview.api);

function pickWithInput() {
  return new Promise((resolve) => {
    const input = document.getElementById('file-input');
    input.value = '';
    input.onchange = () => resolve([...input.files]);
    input.click();
  });
}

/** Abre o seletor de livros e adiciona à estante. Devolve {added, errors}. */
export async function chooseBooks() {
  if (isDesktop()) {
    const paths = await window.pywebview.api.choose_books();
    if (!paths || !paths.length) return { added: [], errors: [] };
    return api.addPaths(paths);
  }
  const files = await pickWithInput();
  if (!files.length) return { added: [], errors: [] };
  return api.upload(files);
}

export async function uploadFiles(files) {
  return api.upload(files);
}

export async function chooseFolder() {
  if (!isDesktop()) return null;
  return window.pywebview.api.choose_folder();
}

export async function reveal(path) {
  if (isDesktop()) return window.pywebview.api.reveal(path);
  return false;
}

export async function openFile(path) {
  if (isDesktop()) return window.pywebview.api.open_file(path);
  return false;
}
