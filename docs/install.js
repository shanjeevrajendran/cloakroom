// Per-agent install tabs and copy buttons; without script every panel shows and the copy buttons stay hidden.
document.querySelectorAll('[data-tabs]').forEach((box) => {
  const tabs = [...box.querySelectorAll('[role="tab"]')];
  const show = (tab, focus) => {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
    });
    if (focus) tab.focus();
  };
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => show(t));
    t.addEventListener('keydown', (e) => {
      const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
      if (step) { e.preventDefault(); show(tabs[(i + step + tabs.length) % tabs.length], true); }
    });
  });
  show(tabs[0]);
});

document.querySelectorAll('.copy').forEach((btn) => btn.addEventListener('click', async () => {
  const code = btn.parentElement.querySelector('code').innerText;
  try { await navigator.clipboard.writeText(code); btn.textContent = 'Copied'; } catch { btn.textContent = 'Select to copy'; }
  setTimeout(() => { btn.textContent = 'Copy'; }, 1400);
}));
