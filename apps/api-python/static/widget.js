// static/widget.js
(function () {
  // --- basic styles injected (you can also keep this in widget.css and <link> it) ---
  const style = document.createElement('style');
  style.textContent = `
  :root {
    --mda-bg:#000;
    --mda-surface:#1a1a1a;
    --mda-border:#2a2a2a;
    --mda-text:#fff;
    --mda-muted:#9ca3af;
    --mda-accent:#FFB906;
    --mda-radius-lg:16px;
    --mda-radius-xl:24px;
    --mda-shadow:0 30px 60px rgba(0,0,0,.8);
  }

  .mda-launcher-btn {
    position: fixed;
    bottom: 20px;
    right: 20px;
    background: var(--mda-accent);
    color:#000;
    border-radius:50%;
    width:56px;
    height:56px;
    border:0;
    box-shadow:0 20px 40px rgba(0,0,0,.7);
    cursor:pointer;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:16px;
    font-weight:600;
    z-index:999999;
  }
  .mda-launcher-btn:active {
    scale:.97;
  }

  .mda-chat-panel {
    position: fixed;
    bottom: 90px;
    right: 20px;
    width:320px;
    max-height:480px;
    background:var(--mda-bg);
    border:1px solid var(--mda-border);
    border-radius:var(--mda-radius-xl);
    box-shadow:var(--mda-shadow);
    color:var(--mda-text);
    font-family:ui-sans-serif,system-ui,Segoe UI,Roboto,Arial;
    display:flex;
    flex-direction:column;
    overflow:hidden;
    z-index:999998;
  }

  .mda-header {
    background:var(--mda-surface);
    border-bottom:1px solid var(--mda-border);
    padding:12px 14px;
    display:flex;
    justify-content:space-between;
    align-items:flex-start;
  }
  .mda-h-left {
    font-size:14px;
    line-height:1.2;
    font-weight:600;
    color:var(--mda-text);
  }
  .mda-h-left span {
    display:block;
    color:var(--mda-muted);
    font-size:12px;
    font-weight:400;
    margin-top:2px;
  }
  .mda-close {
    background:transparent;
    border:0;
    color:var(--mda-muted);
    font-size:14px;
    cursor:pointer;
  }
  .mda-close:hover {
    color:var(--mda-text);
  }

  .mda-messages {
    flex:1;
    overflow-y:auto;
    padding:16px;
    display:flex;
    flex-direction:column;
    gap:12px;
    background:radial-gradient(circle at 20% 20%,rgba(255,185,6,.07) 0%,rgba(0,0,0,0) 70%);
  }

  .mda-bubble {
    max-width:80%;
    padding:10px 12px;
    border-radius:var(--mda-radius-lg);
    font-size:14px;
    line-height:1.4;
    border:1px solid transparent;
    color:var(--mda-text);
    box-shadow:0 16px 32px rgba(0,0,0,.7);
    white-space:pre-wrap;
    word-break:break-word;
  }
  .mda-user   { align-self:flex-end; background:#2a2a2a; border-color:#3a3a3a; }
  .mda-assist { align-self:flex-start; background:#1f1f1f; border-color:#2a2a2a; }
  .mda-assist a { color:var(--mda-accent); text-decoration:underline; }

  .mda-input-row {
    border-top:1px solid var(--mda-border);
    background:var(--mda-surface);
    padding:12px;
    display:flex;
    gap:8px;
  }
  .mda-textarea {
    flex:1;
    background:#0000;
    border:0;
    color:var(--mda-text);
    font-size:14px;
    line-height:1.4;
    resize:none;
    outline:0;
    max-height:100px;
  }
  .mda-textarea::placeholder {
    color:var(--mda-muted);
  }
  .mda-send-btn {
    flex-shrink:0;
    background:var(--mda-accent);
    border:0;
    border-radius:10px;
    font-size:13px;
    font-weight:600;
    color:#000;
    padding:0 12px;
    min-height:32px;
    cursor:pointer;
  }
  .mda-send-btn:active { scale:.97; }
  `;
  document.head.appendChild(style);

  // --- create launcher button ---
  const launcher = document.createElement('button');
  launcher.className = 'mda-launcher-btn';
  launcher.setAttribute('aria-label','Chat with us');
  launcher.innerText = '💬';

  // --- create panel ---
  const panel = document.createElement('div');
  panel.className = 'mda-chat-panel';
  panel.style.display = 'none';
  panel.innerHTML = `
    <div class="mda-header">
      <div class="mda-h-left">
        Mogul Agent
        <span>How can I help today?</span>
      </div>
      <button class="mda-close" aria-label="Close">✕</button>
    </div>
    <div class="mda-messages"></div>
    <form class="mda-input-row">
      <textarea class="mda-textarea" rows="1" placeholder="Ask anything..."></textarea>
      <button class="mda-send-btn" type="submit">Send</button>
    </form>
  `;

  document.body.appendChild(launcher);
  document.body.appendChild(panel);

  // --- internal state ---
  const $messages = panel.querySelector('.mda-messages');
  const $close    = panel.querySelector('.mda-close');
  const $form     = panel.querySelector('.mda-input-row');
  const $ta       = panel.querySelector('.mda-textarea');

  const convoHistory = [];

  // helpers
  function addBubble(role, text) {
    const b = document.createElement('div');
    b.className = 'mda-bubble ' + (role === 'user' ? 'mda-user':'mda-assist');
    if (role === 'user') {
      b.textContent = text;
    } else {
      // linkify urls
      b.innerHTML = (text || "").replace(
        /(https?:\/\/[^\s]+)/g,
        (url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`
      );
    }
    $messages.appendChild(b);
    $messages.scrollTop = $messages.scrollHeight;
  }

  async function sendToBackend() {
    // typing bubble placeholder
    const thinkingBubble = document.createElement('div');
    thinkingBubble.className = 'mda-bubble mda-assist';
    thinkingBubble.style.opacity = '.6';
    thinkingBubble.style.fontStyle = 'italic';
    thinkingBubble.textContent = '…';
    $messages.appendChild(thinkingBubble);
    $messages.scrollTop = $messages.scrollHeight;

    try {
      const res = await fetch('/v1/chat', {
        method:'POST',
        headers:{'content-type':'application/json'},
        body: JSON.stringify({ messages: convoHistory })
      });
      if (!res.ok) {
        const fail = `⚠️ ${res.status} ${res.statusText}`;
        thinkingBubble.style.opacity = '1';
        thinkingBubble.style.fontStyle = 'normal';
        thinkingBubble.textContent = fail;
        return;
      }
      const data = await res.json();
      const assistantMsg = data.message;
      const text = assistantMsg?.content || '⚠️ No response';

      thinkingBubble.remove();
      addBubble('assistant', text);

      convoHistory.push({ role:'assistant', content:text });
    } catch (err) {
      thinkingBubble.style.opacity = '1';
      thinkingBubble.style.fontStyle = 'normal';
      thinkingBubble.textContent = `⚠️ Network error: ${err}`;
    }
  }

  // events
  $form.addEventListener('submit', async (e)=>{
    e.preventDefault();
    const text = $ta.value.trim();
    if (!text) return;
    addBubble('user', text);
    convoHistory.push({ role:'user', content:text });
    $ta.value = '';
    await sendToBackend();
  });

  launcher.addEventListener('click', ()=>{
    panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
    if (panel.style.display === 'flex') {
      $ta.focus();
    }
  });
  $close.addEventListener('click', ()=>{
    panel.style.display = 'none';
  });
})();
