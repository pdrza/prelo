// Mini motor de animação: só o que o carrossel usa (to, fromTo, killTweensOf,
// wrap, easing "expo"). Substitui o GSAP do carrossel de referência, cuja
// licença não combina com um projeto AGPL.

const active = new Set();

export const ease = {
  linear: (t) => t,
  expoOut: (t) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t)),
  expoInOut: (t) => {
    if (t <= 0) return 0;
    if (t >= 1) return 1;
    return t < 0.5 ? Math.pow(2, 20 * t - 10) / 2 : (2 - Math.pow(2, -20 * t + 10)) / 2;
  },
};

export let reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
export function setReduced(v) { reduced = v || matchMedia('(prefers-reduced-motion: reduce)').matches; }

export function to(target, props, { duration = 0.5, ease: e = ease.expoOut, delay = 0, onUpdate, onComplete } = {}) {
  // como o "overwrite: auto" do GSAP: a animação nova assume as propriedades
  for (const tw of active) {
    if (tw.target !== target) continue;
    for (const k of Object.keys(props)) delete tw.props[k];
  }
  const tw = { target, props: { ...props }, start: null, t0: performance.now() + delay * 1000,
               dur: reduced ? 1 : duration * 1000, e, onUpdate, onComplete };
  active.add(tw);
  return tw;
}

export function fromTo(target, from, props, opts) {
  Object.assign(target, from);
  return to(target, props, opts);
}

export function killTweensOf(...targets) {
  for (const tw of [...active]) if (targets.includes(tw.target)) active.delete(tw);
}

export function wrap(min, max, v) {
  const r = max - min;
  return ((((v - min) % r) + r) % r) + min;
}

export function tick(now = performance.now()) {
  for (const tw of [...active]) {
    if (now < tw.t0) continue;
    if (!tw.start) {
      tw.start = {};
      for (const k of Object.keys(tw.props)) tw.start[k] = tw.target[k];
    }
    const p = Math.min(1, (now - tw.t0) / tw.dur);
    const k = tw.e(p);
    for (const key of Object.keys(tw.props)) {
      tw.target[key] = tw.start[key] + (tw.props[key] - tw.start[key]) * k;
    }
    tw.onUpdate && tw.onUpdate();
    if (p >= 1) {
      active.delete(tw);
      tw.onComplete && tw.onComplete();
    }
  }
}
