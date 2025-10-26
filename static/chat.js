// ===== selectors =====
const $msgs    = document.getElementById('messages');
const $form    = document.getElementById('form');
const $input   = document.getElementById('input');      // textarea
const $status  = document.getElementById('status');
const $send    = document.getElementById('send');

// calendar modal bits (optional but safe if present)
const $calModal    = document.getElementById('cal-modal');
const $calClose    = document.getElementById('cal-close');
const $calInline   = document.getElementById('cal-inline');
const $calFallback = document.getElementById('cal-fallback');

let calConfig = { calLink: "", brandColor: "#111827" };
let lastName = "";
let lastEmail = "";
let history = [];

/* --------------------------
   Health check -> status pills
--------------------------- */
(async function pingHealth() {
  try {
    const r = await fetch('/healthz');
    const h = await r.json();

    function pill(label, good = true) {
      const span = document.createElement('span');
      span.className = 'pill ' + (good ? 'ok' : 'bad');
      span.textContent = label + (good ? ' ✅' : ' ❌');
      return span;
    }

    if ($status) {
      $status.innerHTML = ''; // clear existing

      $status.appendChild(pill('server', !!h.ok));
      $status.appendChild(pill('openai', !!h.openai));
      $status.appendChild(pill('firestore', !!h.firestore));

      const modelSpan = document.createElement('span');
      modelSpan.className = 'pill ok';
      modelSpan.textContent = 'model ' + (h.model || 'n/a');
      $status.appendChild(modelSpan);
    }
  } catch (e) {
    if ($status) {
      $status.innerHTML = '';
      const errSpan = document.createElement('span');
      errSpan.className = 'pill bad';
      errSpan.textContent = 'health ❌ ' + e.message;
      $status.appendChild(errSpan);
    }
  }
})();

/* --------------------------
   Helpers
--------------------------- */
function append(role, text, thinking = false) {
  const div = document.createElement('div');
  div.className = 'msg ' + role + (thinking ? ' thinking' : '');

  // assistant bubbles can include links, so allow basic linkify
  if (role === 'assistant') {
    div.innerHTML = linkify(text);
  } else {
    div.textContent = text;
  }

  $msgs.appendChild(div);
  $msgs.scrollTop = $msgs.scrollHeight;
  return div;
}

// convert plain URLs to clickable links
function linkify(str) {
  return str.replace(
    /(https?:\/\/[^\s]+)/g,
    (url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`
  );
}

// auto-grow textarea
function autoResize() {
  $input.style.height = 'auto';
  $input.style.height = Math.min(160, $input.scrollHeight) + 'px';
}
if ($input) {
  $input.addEventListener('input', autoResize);
  autoResize();
}

// capture name/email from user text to pre-fill Cal.com
function captureIdentity(text) {
  const emailMatch = text.match(/([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})/i);
  if (emailMatch) lastEmail = emailMatch[1];

  // crude name parse
  const nameMatch = text.match(/(?:i am|i'm|my name is|name is)\s+([a-z][a-z\s.'-]{1,60})/i);
  if (nameMatch) lastName = nameMatch[1].trim();
}

/* --------------------------
   Calendar modal
--------------------------- */
async function loadCalConfig() {
  try {
    const res = await fetch('/config');
    const cfg = await res.json();
    calConfig.calLink = (cfg.calLink || cfg.link || '').trim();
    calConfig.brandColor = (cfg.brandColor || '#111827').trim();

    if ($calFallback) {
      $calFallback.href = calConfig.calLink
        ? `https://cal.com/${calConfig.calLink}`
        : 'https://cal.com/';
    }
  } catch (err) {
    console.warn('Config fetch failed', err);
  }
}

function openCalModal() {
  if (!$calModal) return;
  $calModal.classList.add('open');
  $calModal.removeAttribute('aria-hidden');
}

function closeCalModal() {
  if (!$calModal) return;
  $calModal.classList.remove('open');
  $calModal.setAttribute('aria-hidden', 'true');
}

if ($calClose) {
  $calClose.addEventListener('click', closeCalModal);
}
if ($calModal) {
  $calModal.addEventListener('click', (e)=> {
    if (e.target === $calModal) closeCalModal();
  });
}

async function renderCalInline() {
  await window.__ensureCalLoaded?.();
  await loadCalConfig();
  if (!window.Cal || !calConfig.calLink || !$calInline) return;

  // clear prior render
  $calInline.innerHTML = "";

  const NS = "chat_cal";

  window.Cal("init", NS, { origin: "https://cal.com" });

  window.Cal.ns[NS]("ui", {
    styles: { branding: { brandColor: calConfig.brandColor } },
    layout: "month_view",
    hideEventTypeDetails: false
  });

  // successful booking handler
  window.Cal.ns[NS]("on", {
    action: "bookingSuccessfulV2",
    callback: (e) => {
      const d = e.detail?.data || {};
      append(
        'assistant',
        `✅ Booked! ${d.uid ? 'Confirmation ' + d.uid + '. ' : ''}` +
        (d.startTime ? `Starts ${new Date(d.startTime).toLocaleString()}. ` : '') +
        (d.videoCallUrl ? `Join: ${d.videoCallUrl}` : '')
      );
      closeCalModal();
    }
  });

  window.Cal.ns[NS]("inline", {
    elementOrSelector: "#cal-inline",
    calLink: calConfig.calLink,
    config: { name: lastName || "", email: lastEmail || "" }
  });
}

async function openCalendar() {
  openCalModal();
  await renderCalInline();
}

// open calendar automatically if assistant is nudging to schedule
function maybeTriggerCalendar(assistantText) {
  const t = (assistantText || "").toLowerCase();
  const trigger = /(when would you like to book|what time works|choose a time|pick a time|ready to schedule|select a time|book a time)/i;
  if (trigger.test(t)) {
    openCalendar();
  }
}

/* --------------------------
   Sending chat (non-stream)
--------------------------- */

$form.addEventListener('submit', async (e)=>{
  e.preventDefault();
  const text = $input.value.trim();
  if (!text) return;

  captureIdentity(text);

  append('user', text);
  history.push({ role: 'user', content: text });

  $input.value = '';
  autoResize();
  $input.focus();

  await sendChatOnce();
});

async function sendChatOnce() {
  const bubble = append('assistant', '…', true /* thinking */);

  try {
    const res = await fetch('/v1/chat', {
      method:'POST',
      headers:{'content-type':'application/json'},
      body: JSON.stringify({ messages: history })
    });

    if (!res.ok) {
      const errText = await res.text().catch(()=>res.statusText);
      bubble.classList.remove('thinking');
      bubble.innerHTML = `⚠️ ${res.status} ${res.statusText}: ${linkify(errText)}`;
      return;
    }

    const data = await res.json();

    // note: backend returns { message: { role, content, ... } }
    const assistantMsg = data.message;
    const text = assistantMsg?.content || "⚠️ No response";

    bubble.classList.remove('thinking');
    bubble.innerHTML = linkify(text);

    history.push({ role:'assistant', content: text });

    maybeTriggerCalendar(text);

  } catch (err) {
    bubble.classList.remove('thinking');
    bubble.textContent = `⚠️ Network error: ${err}`;
    console.error(err);
  }
}
