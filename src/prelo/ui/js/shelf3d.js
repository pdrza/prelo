// Estante em 3D: o carrossel do unveil com capas de livros no lugar dos álbuns.
// Mesma técnica do estudo (Three.js): câmera de FOV 5° (quase sem perspectiva),
// placas finas giradas -30° num trilho diagonal, shader com miolo
// nítido e bordas foscas translúcidas ("vidro"), placa que desliza no hover e
// vira de frente ao abrir.
// Diferença do original: o trilho é finito e cada livro aparece uma vez só
// (o unveil repete a lista até ter 20 placas e dá a volta infinita).
import * as THREE from 'three';
import { ease, fromTo, killTweensOf, reduced, tick, to } from './tween.js';

const SPACING = 0.375;
const BASE = 1.5;
const THICKNESS = 0.0175;
const ANGLE = -Math.PI / 6;
const CULL_Z = 12.5;
const SCROLL_EASE = 0.15;
const WHEEL_STEP = 100;        // px de rolagem (touchpad) para andar um livro

const vertexShader = /* glsl */`
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;
const fragmentShader = /* glsl */`
  uniform vec2 uMeshSize;
  uniform vec2 uImageSize;
  uniform sampler2D uImageTexture;
  uniform sampler2D uBlurTexture;
  varying vec2 vUv;
  void main() {
    vec2 ratio = vec2(
      min((uMeshSize.x / uMeshSize.y) / (uImageSize.x / uImageSize.y), 1.0),
      min((uMeshSize.y / uMeshSize.x) / (uImageSize.y / uImageSize.x), 1.0)
    );
    vec2 uv = vec2(vUv.x * ratio.x + (1.0 - ratio.x) * 0.5, vUv.y * ratio.y + (1.0 - ratio.y) * 0.5);
    vec4 sharp = texture2D(uImageTexture, uv);
    vec4 blur = texture2D(uBlurTexture, uv);
    blur.a *= 0.75;
    float m = 0.15;
    float inner = smoothstep(0.0, m, vUv.x) * (1.0 - smoothstep(1.0 - m, 1.0, vUv.x))
                * smoothstep(0.0, m, vUv.y) * (1.0 - smoothstep(1.0 - m, 1.0, vUv.y));
    gl_FragColor = mix(blur, sharp, inner);
  }
`;

function blurThumb(src) {
  const w = 12;
  const c = Object.assign(document.createElement('canvas'), {
    width: w, height: Math.max(4, Math.round((w * (src.height || src.naturalHeight)) / (src.width || src.naturalWidth))),
  });
  c.getContext('2d').drawImage(src, 0, 0, c.width, c.height);
  const t = new THREE.CanvasTexture(c);
  t.minFilter = t.magFilter = THREE.LinearFilter;
  t.generateMipmaps = false;
  return t;
}

function wrapText(g, text, x, y, maxW, lineH, maxLines) {
  const words = String(text || '').split(/\s+/);
  let line = '';
  let n = 0;
  for (let i = 0; i < words.length; i++) {
    const trial = line ? `${line} ${words[i]}` : words[i];
    if (g.measureText(trial).width > maxW && line) {
      n += 1;
      if (n === maxLines) { g.fillText(`${line}…`, x, y); return; }
      g.fillText(line, x, y);
      line = words[i];
      y += lineH;
    } else line = trial;
  }
  if (line) g.fillText(line, x, y);
}

/** Capa provisória (sem capa ainda, ou estante vazia): cartão monocromático. */
function placeholderArt(item, i) {
  const w = 512;
  const h = Math.round(512 * (item.ratio || 1.5));
  const c = Object.assign(document.createElement('canvas'), { width: w, height: h });
  const g = c.getContext('2d');
  const dark = item.placeholder ? i % 3 === 1 : i % 2 === 0;
  g.fillStyle = dark ? '#0b0b0b' : '#f2f2f2';
  g.fillRect(0, 0, w, h);
  g.fillStyle = dark ? '#ffffff' : '#000000';
  if (item.placeholder) {
    g.font = '400 112px Inter, system-ui, sans-serif';
    g.fillText(item.title, 34, 150);
    g.font = '400 26px Inter, system-ui, sans-serif';
    g.globalAlpha = 0.6;
    wrapText(g, item.subtitle, 38, h - 96, w - 76, 34, 2);
    g.globalAlpha = 1;
    g.strokeStyle = g.fillStyle;
    g.lineWidth = 2;
    g.strokeRect(34, 196, w - 68, 2);
  } else {
    g.font = '400 46px Inter, system-ui, sans-serif';
    wrapText(g, item.title, 40, 92, w - 80, 54, 5);
    g.globalAlpha = 0.6;
    g.font = '400 26px Inter, system-ui, sans-serif';
    wrapText(g, item.subtitle, 40, h - 60, w - 80, 32, 2);
    g.globalAlpha = 1;
  }
  return c;
}

export class Shelf {
  constructor(container, { onOpen, onClose, onClickEmpty } = {}) {
    this.el = container;
    this.onOpen = onOpen;
    this.onClose = onClose;
    this.onClickEmpty = onClickEmpty;
    this.canHover = matchMedia('(hover: hover) and (pointer: fine)').matches;
    this.canvas = document.createElement('canvas');
    container.prepend(this.canvas);
    this.label = container.querySelector('.float-label');

    this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.scene = new THREE.Scene();
    this.stage = new THREE.Group();
    this.scene.add(this.stage);
    this.camera = new THREE.PerspectiveCamera(5, 1, 0.1, 1000);
    this.raycaster = new THREE.Raycaster();
    this.mouse = new THREE.Vector2(1e3, -1e3);
    this.loader = new THREE.TextureLoader();

    this.tiles = [];
    this.count = 0;
    // posição no trilho = índice (fracionário) do livro que está no centro
    this.pos = { target: 0, current: 0 };
    this.wheelAcc = 0;
    this.wheelTimer = 0;
    this.drag = { x: 0, y: 0, from: 0, down: false, active: false, timer: 0 };
    this.intro = { x: 0 };
    this.pointer = { x: 0, y: 0, lx: 0, ly: 0, moved: false };
    this.hovered = null;
    this.active = null;
    this.down = { t: 0, x: 0, y: 0 };
    this.size = { w: 1, h: 1 };
    this.setBackground();

    this._wheel = (e) => this.onWheel(e);
    this._pdown = (e) => this.onPointerDown(e);
    this._pmove = (e) => this.onPointerMove(e);
    this._pup = (e) => this.onPointerUp(e);
    this.canvas.addEventListener('wheel', this._wheel, { passive: false });
    this.canvas.addEventListener('pointerdown', this._pdown);
    window.addEventListener('pointermove', this._pmove);
    window.addEventListener('pointerup', this._pup);
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    this.resize();
    this.loop = (now) => this.frame(now);
    this.running = true;
    requestAnimationFrame(this.loop);
  }

  setBackground() {
    const bg = getComputedStyle(this.el).getPropertyValue('background-color') || '#ffffff';
    this.renderer.setClearColor(new THREE.Color(bg.trim() || '#ffffff'));
  }

  homeCamera() {
    // mesma posição do original, 15% mais longe: o carrossel vive numa janela, não na tela toda
    const aspect = this.size.w / this.size.h;
    const k = 1.15;
    this.camera.position.set(0, (100 / 7.5) * k, (aspect < 1 ? 55 : 35) * k);
    this.camera.lookAt(0, 0, 0);
  }

  resize() {
    const w = Math.max(1, this.el.clientWidth);
    const h = Math.max(1, this.el.clientHeight);
    this.size = { w, h };
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    if (!this.active) this.homeCamera();
  }

  // ------------------------------------------------------------ itens
  setItems(items) {
    const empty = !items.length;
    const source = empty ? [
      { id: null, title: 'PDF', subtitle: 'layout fiel ou texto fluido', ratio: 1.42, placeholder: true },
      { id: null, title: 'EPUB', subtitle: 'Kobo, Apple Books, Enviar para Kindle', ratio: 1.5, placeholder: true },
      { id: null, title: 'AZW3', subtitle: 'Kindle moderno, gerado sem Calibre', ratio: 1.33, placeholder: true },
      { id: null, title: 'MOBI', subtitle: 'Kindles antigos', ratio: 1.5, placeholder: true },
      { id: null, title: 'CBZ', subtitle: 'quadrinhos e mangá', ratio: 1.42, placeholder: true },
    ] : items;
    this.clear();
    this.empty = empty;
    this.count = source.length;
    source.forEach((item, i) => this.addTile(item, i));
    this.el.dataset.placas = String(this.count);   // conferido pelo prelo-app --diagnostico
    // começa no meio da coleção; o trilho entra deslizando
    this.pos.target = this.pos.current = (this.count - 1) / 2;
    this.intro.x = -3;
    to(this.intro, { x: 0 }, { duration: 1.6, ease: ease.expoOut });
    fromTo(this.stage.scale, { x: 0.825, y: 0.825, z: 0.825 }, { x: 1, y: 1, z: 1 },
           { delay: reduced ? 0 : 0.6, duration: 1, ease: ease.expoOut });
  }

  addTile(item, i) {
    const ratio = Math.min(1.8, Math.max(0.6, item.ratio || 1.5));
    const shrink = 1 - (ratio - 1) * 0.5;   // original: placas altas ficam menores
    const w = BASE * shrink;
    const h = BASE * ratio * shrink;
    const art = placeholderArt(item, i);
    const image = new THREE.CanvasTexture(art);
    const blur = blurThumb(art);
    const geometry = new THREE.BoxGeometry(w, h, THICKNESS);
    const material = new THREE.ShaderMaterial({
      vertexShader, fragmentShader, transparent: true,
      uniforms: {
        uImageTexture: { value: image },
        uBlurTexture: { value: blur },
        uImageSize: { value: new THREE.Vector2(art.width, art.height) },
        uMeshSize: { value: new THREE.Vector2(w, h) },
      },
    });
    const mesh = new THREE.Mesh(geometry, material);
    const restX = -(w - BASE) / 2;
    mesh.position.x = restX;
    const slider = new THREE.Group();
    slider.position.x = this.canHover ? 0 : -0.325;
    slider.add(mesh);
    const hit = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }));
    hit.scale.x = 1.5;   // área de hover fixa: não "foge" do mouse quando a placa desliza
    const holder = new THREE.Group();
    holder.add(slider, hit);
    this.stage.add(holder);
    const tile = { i, item, holder, slider, mesh, hit, restX, hovered: false };
    mesh.userData.tile = hit.userData.tile = tile;
    this.tiles.push(tile);

    if (item.cover) {
      this.loader.load(item.cover, (tex) => {
        if (!this.tiles.includes(tile)) { tex.dispose(); return; }
        const u = material.uniforms;
        u.uImageTexture.value.dispose();
        u.uBlurTexture.value.dispose();
        u.uImageTexture.value = tex;
        u.uImageSize.value.set(tex.image.width, tex.image.height);
        u.uBlurTexture.value = blurThumb(tex.image);
      });
    }
  }

  clear() {
    for (const t of this.tiles) {
      this.stage.remove(t.holder);
      t.mesh.geometry.dispose();
      t.mesh.material.uniforms.uImageTexture.value.dispose();
      t.mesh.material.uniforms.uBlurTexture.value.dispose();
      t.mesh.material.dispose();
      t.hit.material.dispose();
    }
    this.tiles = [];
    this.active = null;
    this.hovered = null;
  }

  // ------------------------------------------------------------ trilho
  clampIndex(v) {
    return Math.max(0, Math.min(this.count - 1, v));
  }

  step(n) {
    this.pos.target = this.clampIndex(Math.round(this.pos.target) + n);
  }

  layout() {
    this.pos.current += (this.pos.target - this.pos.current) * SCROLL_EASE;
    const aspect = this.size.w / this.size.h;
    const offset = this.pos.current + this.intro.x;
    for (const t of this.tiles) {
      const x = (t.i - offset) * SPACING;
      t.holder.position.set(x, 0, aspect < 1 ? -x * 6 : -x * aspect * 1.5);
      t.holder.rotation.y = ANGLE;
      t.holder.visible = Math.abs(t.holder.position.z) < CULL_Z;
    }
  }

  setHover(t, on) {
    if (t.hovered === on) return;
    t.hovered = on;
    to(t.slider.position, this.canHover
      ? { x: on ? 2 / 3 : 0, y: on ? -0.1 : 0 }
      : { x: on ? 0.325 : -0.325, y: on ? -0.1 : 0 }, { ease: ease.expoOut, duration: 0.5 });
  }

  updateHover() {
    if (this.active) return;
    const visible = this.tiles.filter((t) => t.holder.visible);
    this.hovered = null;
    if (this.canHover) {
      if (!this.drag.active && this.pointer.moved) {
        this.raycaster.setFromCamera(this.mouse, this.camera);
        const hit = this.raycaster.intersectObjects(visible.map((t) => t.hit), false)[0];
        this.hovered = hit ? hit.object.userData.tile : null;
      }
    } else if (visible.length) {
      this.hovered = visible.reduce((a, b) => (Math.abs(a.holder.position.z) < Math.abs(b.holder.position.z) ? a : b));
    }
    for (const t of this.tiles) this.setHover(t, t === this.hovered);
  }

  setDragActive(on) {
    if (this.drag.active === on) return;
    this.drag.active = on;
    const s = on ? 0.825 : 1;
    to(this.stage.scale, { x: s, y: s, z: s }, { duration: 0.75, ease: ease.expoOut });
  }

  // ------------------------------------------------------------ abrir/fechar
  open(t) {
    this.active = t;
    this.pos.target = this.pos.current = t.i;   // ao fechar, o trilho fica centrado neste livro
    for (const o of this.tiles) if (o !== t) o.holder.visible = false;
    const aspect = this.size.w / this.size.h;
    const targetX = aspect > 1.15 ? Math.min(1.1, 0.42 * aspect) : 0;  // capa à direita, detalhes à esquerda
    const d = (1.25 / 3) * 2;
    to(t.slider.position, { x: 0, y: 0 }, { ease: ease.expoOut, duration: 0.5 });
    to(t.mesh.position, { x: 0 }, { ease: ease.expoOut, duration: d });
    to(t.holder.position, { x: targetX, z: 0 }, { ease: ease.expoOut, duration: d });
    to(t.holder.rotation, { y: 0 }, { ease: ease.expoInOut, duration: 1.25 });
    to(this.stage.scale, { x: 0.825, y: 0.825, z: 0.825 }, { ease: ease.expoOut, duration: 0.75 });
    to(this.camera.position, { x: 0, y: 0, z: this.size.w < 640 ? 35 : 30 },
       { ease: ease.expoInOut, duration: 1.25, onUpdate: () => this.camera.lookAt(0, 0, 0) });
    this.label.style.opacity = 0;
    if (this.onOpen) this.onOpen(t.item);
  }

  close() {
    const t = this.active;
    if (!t) return;
    killTweensOf(t.holder.position, t.holder.rotation, t.mesh.position, t.slider.position, this.camera.position);
    t.mesh.position.x = t.restX;
    for (const o of this.tiles) {
      o.hovered = false;
      o.slider.position.set(this.canHover ? 0 : -0.325, 0, 0);
    }
    this.active = null;
    this.homeCamera();                       // original: corte seco + trilho entra deslizando
    this.intro.x = -3;
    to(this.intro, { x: 0 }, { ease: ease.expoOut, duration: 1.6 });
    to(this.stage.scale, { x: 1, y: 1, z: 1 }, { ease: ease.expoOut, duration: 1 });
    if (this.onClose) this.onClose();
  }

  openById(id) {
    const t = this.tiles.find((x) => x.item.id === id);
    if (t) {
      if (this.active) this.close();
      this.open(t);
    }
  }

  // ------------------------------------------------------------ entrada
  setMouse(e) {
    const r = this.canvas.getBoundingClientRect();
    this.pointer.x = e.clientX - r.left;
    this.pointer.y = e.clientY - r.top;
    this.pointer.moved = true;
    this.mouse.set((this.pointer.x / r.width - 0.5) * 2, -(this.pointer.y / r.height - 0.5) * 2);
  }

  onWheel(e) {
    e.preventDefault();
    if (this.active) return;
    const delta = (e.deltaY + e.deltaX) * (e.deltaMode === 1 ? 40 : 1);   // Firefox manda "linhas"
    if (Math.abs(delta) >= 50) {             // roda do mouse: um livro por dente
      this.step(Math.sign(delta));
      return;
    }
    this.wheelAcc += delta;                  // touchpad: acumula os movimentos pequenos
    if (Math.abs(this.wheelAcc) >= WHEEL_STEP) {
      this.step(Math.sign(this.wheelAcc));
      this.wheelAcc = 0;
    }
    clearTimeout(this.wheelTimer);
    this.wheelTimer = setTimeout(() => { this.wheelAcc = 0; }, 250);
  }

  key(e) {
    if (e.key === 'Escape') { this.close(); return true; }
    if (this.active) return false;
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') { this.step(1); return true; }
    if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') { this.step(-1); return true; }
    if (e.key === 'Home') { this.pos.target = 0; return true; }
    if (e.key === 'End') { this.pos.target = this.count - 1; return true; }
    if (e.key === 'Enter') {
      const t = this.hovered || this.tiles[Math.round(this.pos.current)];
      if (t && t.item.id) this.open(t);
      return Boolean(t);
    }
    return false;
  }

  onPointerDown(e) {
    this.setMouse(e);
    this.down = { t: performance.now(), x: e.clientX, y: e.clientY };
    if (this.active) return;
    this.drag.down = true;
    this.drag.x = e.clientX;
    this.drag.y = e.clientY;
    this.drag.from = this.pos.target;
    clearTimeout(this.drag.timer);
    this.drag.timer = setTimeout(() => this.setDragActive(true), 150);   // só vira arraste após 150 ms
  }

  onPointerMove(e) {
    if (e.target !== this.canvas && !this.drag.down) { this.pointer.moved = false; return; }
    this.setMouse(e);
    if (this.drag.down) {  // arrastar para o lado ou para cima percorre o trilho
      const d = (e.clientX - this.drag.x) - (e.clientY - this.drag.y);
      this.pos.target = this.clampIndex(this.drag.from - d / (this.canHover ? 140 : 70));
    }
  }

  onPointerUp(e) {
    const wasDown = this.drag.down;
    const isClick = performance.now() - this.down.t < 220 && Math.hypot(e.clientX - this.down.x, e.clientY - this.down.y) < 6;
    this.drag.down = false;
    clearTimeout(this.drag.timer);
    this.setDragActive(false);
    if (wasDown && !isClick) this.pos.target = this.clampIndex(Math.round(this.pos.target));  // encaixa num livro
    if (!isClick || e.target !== this.canvas) return;
    if (this.active) { this.close(); return; }
    if (!wasDown) return;
    let t = this.hovered;
    if (!this.canHover) {
      this.raycaster.setFromCamera(this.mouse, this.camera);
      const hit = this.raycaster.intersectObjects(this.tiles.filter((o) => o.holder.visible).map((o) => o.mesh), false)[0];
      t = hit ? hit.object.userData.tile : null;
    }
    if (!t) return;
    if (!t.item.id) { if (this.onClickEmpty) this.onClickEmpty(); return; }
    this.open(t);
  }

  // ------------------------------------------------------------ quadro
  frame(now) {
    if (!this.running) return;
    tick(now);
    if (!this.active) this.layout();
    this.scene.updateMatrixWorld();
    this.updateHover();
    const p = this.pointer;
    p.lx += (p.x - p.lx) * 0.1;
    p.ly += (p.y - p.ly) * 0.1;
    if (this.label) {
      this.label.style.transform = `translate(${p.lx}px, ${p.ly}px)`;
      this.label.style.opacity = this.canHover && this.hovered && !this.active ? 1 : 0;
      if (this.hovered) this.label.textContent = this.hovered.item.id ? this.hovered.item.title : 'Adicionar um livro';
    }
    this.canvas.style.cursor = this.active ? 'default' : this.hovered && this.canHover ? 'pointer' : this.drag.active ? 'grabbing' : 'grab';
    this.renderer.render(this.scene, this.camera);
    requestAnimationFrame(this.loop);
  }

  destroy() {
    this.running = false;
    this.resizeObserver.disconnect();
    this.canvas.removeEventListener('wheel', this._wheel);
    this.canvas.removeEventListener('pointerdown', this._pdown);
    window.removeEventListener('pointermove', this._pmove);
    window.removeEventListener('pointerup', this._pup);
    this.clear();
    this.renderer.dispose();
    this.canvas.remove();
  }
}
