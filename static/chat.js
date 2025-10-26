// ===== selectors =====
const $msgs    = document.getElementById('messages');
const $form    = document.getElementById('form');
const $input   = document.getElementById('input');      // textarea
const $status  = document.getElementById('status');
const $send    = document.getElementById('send');
const $mic     = document.getElementById('mic');
const $player  = document.getElementById('voicePlayer');

// NEW: mic wrap for glow
const $micWrap = document.getElementById('micWrap');

// calendar modal bits ...
const $calModal    = document.getElementById('cal-modal');
const $calClose    = document.getElementById('cal-close');
const $calInline   = document.getElementById('cal-inline');
const $calFallback = document.getElementById('cal-fallback');

// state
let calConfig = { calLink: "", brandColor: "#111827" };
let lastName = "";
let lastEmail = "";
let history = [];

// voice recorder state
let mediaRecorder = null;
let micChunks = [];
let isRecording = false;

// tracks whether the last user message came from voice capture
let lastCaptureWasVoice = false;

// NEW: track the "Listening..." bubble DOM node so we can upgrade it later
let listeningBubbleEl = null;

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
      $status.innerHTML = '';
      $status.classList.add('status-col');

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
      $status.classList.add('status-col');
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
function linkify(str) {
  return (str || "").replace(
    /(https?:\/\/[^\s]+)/g,
    (url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`
  );
}

function append(role, text, thinking = false) {
  const div = document.createElement('div');
  div.className = 'msg ' + role + (thinking ? ' thinking' : '');

  if (role === 'assistant') {
    div.innerHTML = linkify(text);
  } else {
    div.textContent = text;
  }

  $msgs.appendChild(div);
  $msgs.scrollTop = $msgs.scrollHeight;
  return div;
}

// special append just for listening bubble
function showListeningBubble() {
  listeningBubbleEl = document.createElement('div');
  listeningBubbleEl.className = 'msg system';
  listeningBubbleEl.innerHTML = `<span>Listening</span><span class="listening-dots"></span>`;
  $msgs.appendChild(listeningBubbleEl);
  $msgs.scrollTop = $msgs.scrollHeight;
}

// turn listening bubble into either user voice message or an error
function finalizeListeningBubble(textOrError, isError=false) {
  if (!listeningBubbleEl) return;

  if (isError) {
    listeningBubbleEl.className = 'msg assistant';
    listeningBubbleEl.textContent = textOrError;
  } else {
    listeningBubbleEl.className = 'msg user';
    listeningBubbleEl.textContent = "(voice) " + textOrError;
  }

  listeningBubbleEl = null;
}

// capture name/email from user text
function captureIdentity(text) {
  const emailMatch = text.match(/([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})/i);
  if (emailMatch) lastEmail = emailMatch[1];

  const nameMatch = text.match(/(?:i am|i'm|my name is|name is)\s+([a-z][a-z\s.'-]{1,60})/i);
  if (nameMatch) lastName = nameMatch[1].trim();
}

/* --------------------------
   Calendar stuff
--------------------------- */
async function loadCalConfig() { /* unchanged */ }
function openCalModal() { /* unchanged */ }
function closeCalModal() { /* unchanged */ }
if ($calClose) { $calClose.addEventListener('click', closeCalModal); }
if ($calModal) {
  $calModal.addEventListener('click', (e)=> {
    if (e.target === $calModal) closeCalModal();
  });
}
async function renderCalInline() { /* unchanged */ }
async function openCalendar() { openCalModal(); await renderCalInline(); }
function maybeTriggerCalendar(assistantText) {
  const t = (assistantText || "").toLowerCase();
  const trigger = /(when would you like to book|what time works|choose a time|pick a time|ready to schedule|select a time|book a time)/i;
  if (trigger.test(t)) {
    openCalendar();
  }
}

/* --------------------------
   Send chat to backend
--------------------------- */
async function sendChatOnce() {
  const bubble = append('assistant', '…', true);

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
    const assistantMsg = data.message;
    const text = assistantMsg?.content || "⚠️ No response";

    bubble.classList.remove('thinking');
    bubble.innerHTML = linkify(text);

    history.push({ role:'assistant', content: text });

    maybeTriggerCalendar(text);

    // ✅ Only speak out loud if last message was voice
    if (lastCaptureWasVoice) {
      speakText(text);
    }

  } catch (err) {
    bubble.classList.remove('thinking');
    bubble.textContent = `⚠️ Network error: ${err}`;
    console.error(err);
  }
}

/* --------------------------
   TEXT SUBMIT
--------------------------- */
$form.addEventListener('submit', async (e)=>{
  e.preventDefault();
  const text = $input.value.trim();
  if (!text) return;

  captureIdentity(text);

  append('user', text);
  history.push({ role: 'user', content: text });

  $input.value = '';
  $input.focus();

  updateComposerMode();

  // typed message → no voice reply
  lastCaptureWasVoice = false;

  await sendChatOnce();
});

/* --------------------------
   VOICE HELPERS
--------------------------- */
async function handleVoiceMessage(audioBlob){
  const fd = new FormData();
  fd.append('audio', audioBlob, 'speech.webm');

  let heardText = '';
  let sttError = null;

  try {
    const sttRes = await fetch('/v1/stt', {
      method:'POST',
      body: fd
    });
    const sttData = await sttRes.json();
    heardText = sttData.text || '';
    sttError = sttData.error || null;
  } catch (err){
    sttError = err.message || String(err);
  }

  if (!heardText) {
    finalizeListeningBubble(
      sttError
        ? "I couldn't transcribe that (STT error)."
        : "I couldn't hear anything.",
      true
    );
    console.warn("STT error detail:", sttError);
    return;
  }

  captureIdentity(heardText);

  finalizeListeningBubble(heardText, false);
  history.push({ role:'user', content: heardText });

  // voice message → enable TTS for next assistant reply
  lastCaptureWasVoice = true;

  await sendChatOnce();
}

/* --------------------------
   SPEAK TEXT
--------------------------- */
async function speakText(text){
  if (!text || !$player) return;
  try{
    const ttsRes = await fetch('/v1/tts', {
      method:'POST',
      headers:{'content-type':'application/json'},
      body: JSON.stringify({ text })
    });

    const replyBlob = await ttsRes.blob();
    const url = URL.createObjectURL(replyBlob);
    $player.src = url;
    $player.play().catch(()=>{});
  }catch(err){
    console.error("TTS failed", err);
  }
}

/* --------------------------
   MIC BUTTON (press + hold)
--------------------------- */
async function startRecording(){
  if(isRecording) return;
  isRecording = true;

  if ($mic)    $mic.classList.add('recording');
  if ($micWrap)$micWrap.classList.add('recording');

  showListeningBubble();

  micChunks = [];

  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

  mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });

  mediaRecorder.ondataavailable = (e)=>{
    if(e.data.size > 0){
      micChunks.push(e.data);
    }
  };

  mediaRecorder.start();
}

async function stopRecording(){
  if(!isRecording) return;
  isRecording = false;

  if ($mic)    $mic.classList.remove('recording');
  if ($micWrap)$micWrap.classList.remove('recording');

  return new Promise((resolve)=>{
    mediaRecorder.onstop = async ()=>{
      const blob = new Blob(micChunks, { type: 'audio/webm;codecs=opus' });
      micChunks = [];
      await handleVoiceMessage(blob);
      resolve();
    };
    mediaRecorder.stop();
  });
}

// bind press+hold exactly like your original code
if ($mic) {
  $mic.addEventListener('mousedown', startRecording);
  $mic.addEventListener('touchstart', (e)=>{
    e.preventDefault();
    startRecording();
  });
}
window.addEventListener('mouseup', stopRecording);
window.addEventListener('touchend', stopRecording);

/* --------------------------
   MIC/SEND SWAP LIKE CHATGPT
--------------------------- */
function updateComposerMode(){
  const hasText = $input.value.trim().length > 0;
  if (hasText) {
    $form.classList.add('typing-mode');
    if (isRecording) stopRecording();
  } else {
    $form.classList.remove('typing-mode');
  }
}
$input.addEventListener('input', updateComposerMode);
updateComposerMode();
