// ---- Show server (UTC) dates in the visitor's local time ----
function formatDate(el) {
  const d = new Date(el.dataset.date.replace(' ', 'T') + 'Z');
  if (isNaN(d)) return;
  el.textContent = el.dataset.short
    ? d.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString([], { day: 'numeric', month: 'long', year: 'numeric' });
}
document.querySelectorAll('[data-date]').forEach(formatDate);

// ---- Chat (runs only on the chat page) ----
(() => {
  const box = document.getElementById('chat-box');
  if (!box) return;
  const form = document.getElementById('chat-form');
  const input = document.getElementById('chat-input');
  const base = box.dataset.url;
  const me = Number(box.dataset.me);
  const shown = new Set([...box.querySelectorAll('.msg')].map((m) => m.dataset.id));
  let lastId = Math.max(0, ...[...shown].map(Number));
  const scrollDown = () => { box.scrollTop = box.scrollHeight; };

  function addMessage(m) {
    if (shown.has(String(m.id))) return;
    shown.add(String(m.id));
    const empty = document.getElementById('chat-empty');
    if (empty) empty.remove();

    const wrap = document.createElement('div');
    wrap.className = 'msg ' + (m.sender_id === me ? 'me' : 'them');
    wrap.dataset.id = m.id;
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = m.body;                 // textContent = safe from XSS
    const time = document.createElement('div');
    time.className = 'time';
    time.dataset.date = m.created_at;
    time.dataset.short = '1';
    formatDate(time);
    wrap.append(bubble, time);
    box.appendChild(wrap);
    lastId = Math.max(lastId, m.id);
  }

  async function poll() {
    if (document.hidden) return;
    try {
      const res = await fetch(`${base}/messages?after=${lastId}`);
      if (!res.ok) return;
      const list = await res.json();
      if (list.length) { list.forEach(addMessage); scrollDown(); }
    } catch (e) { /* try again on the next tick */ }
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = input.value.trim();
    if (!body) return;
    input.value = '';
    try {
      const res = await fetch(`${base}/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body }),
      });
      if (!res.ok) throw new Error();
      addMessage(await res.json());
      scrollDown();
    } catch (err) {
      input.value = body;
      alert('Could not send your message. Please try again.');
    }
  });

  scrollDown();
  setInterval(poll, 3000);
})();