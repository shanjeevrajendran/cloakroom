// Site behaviour: the swap switch, the ticket board, the pinned output, and section entrances.
// Without this script every pane, row and fragment stays visible.
(() => {
  const root = document.documentElement;
  root.classList.add('site-js');
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // The swap: On disk / What the agent sees
  document.querySelectorAll('[data-swap]').forEach((box) => {
    const buttons = [...box.querySelectorAll('[data-set]')];
    box.querySelectorAll('.pane').forEach((pane) => pane.querySelectorAll('mark').forEach((m, k) => m.style.setProperty('--k', k)));
    let touched = false;
    const show = (which) => {
      box.dataset.show = which;
      buttons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.set === which)));
    };
    buttons.forEach((b) => b.addEventListener('click', () => { touched = true; show(b.dataset.set); }));
    show('disk');
    // Once, after the page settles, the values are handed in and the placeholders come back.
    setTimeout(() => { if (!touched) show('seen'); }, still ? 600 : 2200);
  });

  // The ticket board: one row open at a time
  document.querySelectorAll('[data-board]').forEach((board) => {
    const rows = [...board.querySelectorAll('.row')];
    rows.forEach((row) => row.querySelector('.row-head').addEventListener('click', () => {
      rows.forEach((r) => {
        const on = r === row;
        r.classList.toggle('is-open', on);
        r.querySelector('.row-head').setAttribute('aria-expanded', String(on));
      });
    }));
  });

  // The pinned output: controls and scroll both set it
  const out = document.querySelector('[data-out]');
  if (out) {
    const ctls = [...document.querySelectorAll('.ctl[data-set]')];
    const set = (state) => {
      out.dataset.state = state;
      ctls.forEach((c) => c.setAttribute('aria-pressed', String(c.dataset.set === state)));
    };
    ctls.forEach((c) => c.addEventListener('click', () => set(c.dataset.set)));
    if ('IntersectionObserver' in window) {
      const io = new IntersectionObserver((entries) => entries.forEach((e) => { if (e.isIntersecting) set(e.target.dataset.step); }), { rootMargin: '-45% 0px -45% 0px' });
      document.querySelectorAll('.feat[data-step]').forEach((f) => io.observe(f));
    }
  }

  // Entrances: the parts of each section rise in turn, once
  const parts = [...document.querySelectorAll('main > :not(.hero) [data-rise], .row, .feat > *, .tiles details, .group-aside, .defs-board > div, .leaves dl > div, .qa-list > div, .ticket > *')];
  const seen = new Map();
  parts.forEach((el) => {
    const parent = el.closest('section, article') || document.body;
    const n = seen.get(parent) || 0;
    el.style.setProperty('--d', Math.min(n, 8));
    // Played once: a resize across the breakpoint must not replay it.
    el.addEventListener('animationend', (e) => { if (e.target === el) el.classList.add('done'); });
    seen.set(parent, n + 1);
  });
  if (still) { parts.forEach((el) => el.classList.add('in')); return; }
  // Reveal every part whose top has come within the screen, including any the reader jumped past.
  let pending = parts, queued = false;
  const check = () => {
    queued = false;
    const line = innerHeight * 0.92;
    pending = pending.filter((el) => {
      if (el.getBoundingClientRect().top < line) { el.classList.add('in'); return false; }
      return true;
    });
    if (!pending.length) removeEventListener('scroll', onScroll);
  };
  const onScroll = () => { if (!queued) { queued = true; requestAnimationFrame(check); } };
  addEventListener('scroll', onScroll, { passive: true });
  addEventListener('resize', onScroll);
  check();
})();
