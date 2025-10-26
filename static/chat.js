// ===== DOM refs (we will reassign after transition) =====
let $msgs          = document.getElementById('messages');

// HERO composer refs
let $form          = document.getElementById('form');
let $input         = document.getElementById('input');
let $mic           = document.getElementById('mic');
let $micWrap       = document.getElementById('micWrap');
let $send          = document.getElementById('send');

// DOCKED composer refs
const $formDock    = document.getElementById('form-docked');
const $inputDock   = document.getElementById('input-docked');
const $micDock     = document.getElementById('mic-docked');
const $micWrapDock = document.getElementById('micWrap-docked');
const $sendDock    = document.getElementById('send-docked');

// audio player
const $player      = document.getElementById('voicePlayer');

// calendar modal bits
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

// whether last user message was voice
let lastCaptureWasVoice = false;

// "Listening..." bubble node
let listeningBubbleEl = null;

// whether we've already switched from hero-mode -> chat mode
let chatStarted = false;

/* --------------------------
   Internal helpers
--------------------------- */

function activateDockMode() {
  if (chatStarted) return;
  chatStarted = true;

  // copy any text in hero input into docked input
  $inputDock.value = $input.value;

  // flip body classes
  document.body.classList.remove('hero-mode');
  document.body.classList.add('has-started');

  // now point our working refs at the docked elements
  $form    = $formDock;
  $input   = $inputDock;
  $mic     = $micDock;
  $micWrap = $micWrapDock;
  $send    = $sendDock;

  // bind composer events to docked controls now that they are "active"
  wireComposerEvents();
}

function linkify(str) {
  return (str || "").replace(
    /(https?:\/\/[^\s]+)/g,
    (url) =>
      `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`
  );
}

// add a message bubble
function append(role, text, thinking = false) {
  // first real chat bubble triggers dock mode
  if ((role === 'user' || role === 'assistant') && !chatStarted) {
    activateDockMode();
  }

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

// show "Listening..." temp bubble
function showListeningBubble() {
  if (!chatStarted) activateDockMode();

  listeningBubbleEl = document.createElement('div');
  listeningBubbleEl.className = 'msg system';
  listeningBubbleEl.innerHTML =
    `<span>Listening</span><span class="listening-dots"></span>`;
  $msgs.appendChild(listeningBubbleEl);
  $msgs.scrollTop = $msgs.scrollHeight;
}

// finalize listening -> user text or error
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

// store detected name/email
function captureIdentity(text) {
  const emailMatch = text.match(/([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})/i);
  if (emailMatch) lastEmail = emailMatch[1];

  const nameMatch = text.match(/(?:i am|i'm|my name is|name is)\s+([a-z][a-z\s.'-]{1,60})/i);
  if (nameMatch) lastName = nameMatch[1].trim();
}

/* --------------------------
   Calendar stuff (stubs)
--------------------------- */
async function loadCalConfig() { /* hook up your /v1/cal-config if you have it */ }
function openCalModal() { if ($calModal) $calModal.classList.add('open'); }
function closeCalModal() { if ($calModal) $calModal.classList.remove('open'); }

if ($calClose) {
  $calClose.addEventListener('click', closeCalModal);
}
if ($calModal) {
  $calModal.addEventListener('click', (e)=> {
    if (e.target === $calModal) closeCalModal();
  });
}

async function renderCalInline() { /* you can embed Cal.com iframe here */ }
async function openCalendar() { openCalModal(); await renderCalInline(); }

function maybeTriggerCalendar(assistantText) {
  const t = (assistantText || "").toLowerCase();
  const trigger = /(when would you like to book|what time works|choose a time|pick a time|ready to schedule|select a time|book a time)/i;
  if (trigger.test(t)) {
    openCalendar();
  }
}

/* --------------------------
   Backend chat send
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
      bubble.innerHTML =
        `⚠️ ${res.status} ${res.statusText}: ${linkify(errText)}`;
      return;
    }

    const data = await res.json();
    const assistantMsg = data.message;
    const text = assistantMsg?.content || "⚠️ No response";

    bubble.classList.remove('thinking');
    bubble.innerHTML = linkify(text);

    history.push({ role:'assistant', content: text });

    maybeTriggerCalendar(text);

    // speak only if last message was voice
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
   Handle text submit (current active composer)
--------------------------- */
async function handleSubmit(e){
  e.preventDefault();
  const text = $input.value.trim();
  if (!text) return;

  captureIdentity(text);

  append('user', text);
  history.push({ role: 'user', content: text });

  $input.value = '';
  $input.focus();

  updateComposerMode();

  // typed msg -> do not auto-speak assistant
  lastCaptureWasVoice = false;

  await sendChatOnce();
}

/* --------------------------
   Voice helpers
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

  // voice message -> we want assistant to talk back
  lastCaptureWasVoice = true;

  await sendChatOnce();
}

/* --------------------------
   TTS playback of assistant
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
   Mic press+hold -> record
--------------------------- */
async function startRecording(){
  if(isRecording) return;
  isRecording = true;

  if ($mic)     $mic.classList.add('recording');
  if ($micWrap) $micWrap.classList.add('recording');

  // show "Listening..." bubble immediately
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

  if ($mic)     $mic.classList.remove('recording');
  if ($micWrap) $micWrap.classList.remove('recording');

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

/* --------------------------
   Typing-mode toggle
--------------------------- */
function updateComposerMode(){
  const hasText = $input.value.trim().length > 0;
  // add/remove typing-mode class on the ACTIVE form
  if (hasText) {
    $form.classList.add('typing-mode');
    if (isRecording) stopRecording();
  } else {
    $form.classList.remove('typing-mode');
  }
}

/* --------------------------
   Wire up listeners for the ACTIVE composer
   (we call this once at load for hero,
    and again when we switch to dock mode)
--------------------------- */
function wireComposerEvents() {
  // remove previous listeners first to avoid stacking duplicates
  $form.onsubmit = null;
  $input.oninput = null;

  if ($mic) {
    $mic.onmousedown = null;
    $mic.ontouchstart = null;
  }
  window.onmouseup = null;
  window.ontouchend = null;

  // submit
  $form.addEventListener('submit', handleSubmit);

  // typing-mode swap mic/send
  $input.addEventListener('input', updateComposerMode);
  updateComposerMode();

  // mic press+hold
  if ($mic) {
    $mic.addEventListener('mousedown', startRecording);
    $mic.addEventListener('touchstart', (e)=>{
      e.preventDefault();
      startRecording();
    });
  }
  window.addEventListener('mouseup', stopRecording);
  window.addEventListener('touchend', stopRecording);
}

/* --------------------------
   Init
--------------------------- */

// set up listeners for initial hero composer
wireComposerEvents();
