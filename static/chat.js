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

// NEW: history sidebar and scroll button
const $historyBtn  = document.getElementById('history-btn');
const $historySidebar = document.getElementById('history-sidebar');
const $historyClose = document.getElementById('history-close');
const $historyList = document.getElementById('history-list');
const $scrollBtn   = document.getElementById('scroll-to-bottom');
const $waveform    = document.getElementById('waveform');

// state
let calConfig = { calLink: "", brandColor: "#111827" };
let lastName = "";
let lastEmail = "";
let history = [];

// NEW: session management
let sessionId = null;
let autoScroll = true;

// voice recorder state
let mediaRecorder = null;
let micChunks = [];
let isRecording = false;
let audioContext = null;
let analyser = null;
let animationId = null;

// whether last user message was voice
let lastCaptureWasVoice = false;

// "Listening..." bubble node
let listeningBubbleEl = null;

// whether we've already switched from hero-mode -> chat mode
let chatStarted = false;

// NEW: typing indicator reference
let typingIndicatorEl = null;

/* --------------------------
   SESSION PERSISTENCE
--------------------------- */

function initSession() {
  // Get or create session ID
  sessionId = localStorage.getItem('mogul_session_id');
  if (!sessionId) {
    sessionId = 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    localStorage.setItem('mogul_session_id', sessionId);
  }
  
  // Load conversation history
  const saved = localStorage.getItem(`mogul_history_${sessionId}`);
  if (saved) {
    try {
      const data = JSON.parse(saved);
      history = data.messages || [];
      lastName = data.identity?.name || '';
      lastEmail = data.identity?.email || '';
      
      // Restore messages to UI
      if (history.length > 0) {
        activateDockMode();
        history.forEach(msg => {
          append(msg.role === 'user' ? 'user' : 'assistant', msg.content, false);
        });
        showContinueBanner();
      }
    } catch (e) {
      console.warn('Failed to restore session:', e);
    }
  }
  
  // Load all sessions for history sidebar
  loadHistorySidebar();
}

function saveSession() {
  if (!sessionId) return;
  
  const data = {
    sessionId,
    timestamp: Date.now(),
    messages: history,
    identity: { name: lastName, email: lastEmail }
  };
  
  localStorage.setItem(`mogul_history_${sessionId}`, JSON.stringify(data));
  
  // Update sessions list
  let sessions = JSON.parse(localStorage.getItem('mogul_sessions') || '[]');
  sessions = sessions.filter(s => s.id !== sessionId);
  sessions.unshift({ 
    id: sessionId, 
    timestamp: data.timestamp,
    preview: history[0]?.content.substring(0, 50) || 'New conversation'
  });
  sessions = sessions.slice(0, 20); // Keep last 20 sessions
  localStorage.setItem('mogul_sessions', JSON.stringify(sessions));
}

function showContinueBanner() {
  const banner = document.createElement('div');
  banner.className = 'continue-banner';
  banner.innerHTML = `
    <span>📝 Continuing previous conversation</span>
    <button onclick="startNewSession()">Start Fresh</button>
  `;
  $msgs.insertBefore(banner, $msgs.firstChild);
}

function startNewSession() {
  sessionId = 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
  localStorage.setItem('mogul_session_id', sessionId);
  history = [];
  lastName = '';
  lastEmail = '';
  $msgs.innerHTML = '';
  document.querySelector('.continue-banner')?.remove();
  location.reload();
}

// Make startNewSession available globally for the banner button
window.startNewSession = startNewSession;

/* --------------------------
   HISTORY SIDEBAR
--------------------------- */

function loadHistorySidebar() {
  if (!$historyList) return;
  
  const sessions = JSON.parse(localStorage.getItem('mogul_sessions') || '[]');
  $historyList.innerHTML = '';
  
  sessions.forEach(session => {
    const item = document.createElement('div');
    item.className = 'history-item' + (session.id === sessionId ? ' active' : '');
    const date = new Date(session.timestamp).toLocaleDateString();
    item.innerHTML = `
      <div class="history-preview">${session.preview}</div>
      <div class="history-date">${date}</div>
    `;
    item.onclick = () => loadSession(session.id);
    $historyList.appendChild(item);
  });
}

function loadSession(id) {
  sessionId = id;
  localStorage.setItem('mogul_session_id', id);
  location.reload();
}

function toggleHistorySidebar() {
  $historySidebar?.classList.toggle('open');
}

if ($historyBtn) $historyBtn.addEventListener('click', toggleHistorySidebar);
if ($historyClose) $historyClose.addEventListener('click', toggleHistorySidebar);

/* --------------------------
   SCROLL MANAGEMENT
--------------------------- */

function setupScrollManagement() {
  if (!$scrollBtn) return;
  
  const checkScroll = () => {
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    const scrollHeight = document.documentElement.scrollHeight;
    const clientHeight = document.documentElement.clientHeight;
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 150;
    autoScroll = isNearBottom;
    $scrollBtn.style.display = isNearBottom ? 'none' : 'flex';
  };
  
  window.addEventListener('scroll', checkScroll);
  $scrollBtn.addEventListener('click', () => {
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  });
}

function scrollToBottom() {
  if (autoScroll) {
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  }
}

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
  if ((role === 'user' || role === 'assistant') && !chatStarted) {
    activateDockMode();
  }

  const div = document.createElement('div');
  div.className = 'msg ' + role + (thinking ? ' thinking' : '');

  if (role === 'assistant') div.innerHTML = linkify(text);
  else div.textContent = text;

  $msgs.appendChild(div);
  scrollToBottom();
  return div;
}

// NEW: show typing indicator
function showTypingIndicator() {
  removeTypingIndicator();
  
  typingIndicatorEl = document.createElement('div');
  typingIndicatorEl.className = 'msg assistant typing-indicator';
  typingIndicatorEl.innerHTML = '<span></span><span></span><span></span>';
  
  $msgs.appendChild(typingIndicatorEl);
  scrollToBottom();
}

function removeTypingIndicator() {
  if (typingIndicatorEl) {
    typingIndicatorEl.remove();
    typingIndicatorEl = null;
  }
}

// show "Listening..." temp bubble
function showListeningBubble() {
  if (!chatStarted) activateDockMode();

  listeningBubbleEl = document.createElement('div');
  listeningBubbleEl.className = 'msg system';
  listeningBubbleEl.innerHTML =
    `<span>Listening</span><span class="listening-dots"></span>`;
  $msgs.appendChild(listeningBubbleEl);
  scrollToBottom();
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
  
  saveSession();
}

/* --------------------------
   Calendar stuff (stubs)
--------------------------- */
async function loadCalConfig() {}
function openCalModal() { if ($calModal) $calModal.classList.add('open'); }
function closeCalModal() { if ($calModal) $calModal.classList.remove('open'); }

if ($calClose) $calClose.addEventListener('click', closeCalModal);
if ($calModal) {
  $calModal.addEventListener('click', (e)=> { if (e.target === $calModal) closeCalModal(); });
}
async function renderCalInline() {}
async function openCalendar() { openCalModal(); await renderCalInline(); }

function maybeTriggerCalendar(assistantText) {
  const t = (assistantText || "").toLowerCase();
  const trigger = /(when would you like to book|what time works|choose a time|pick a time|ready to schedule|select a time|book a time)/i;
  if (trigger.test(t)) openCalendar();
}

/* --------------------------
   Backend chat send
--------------------------- */
async function sendChatOnce() {
  showTypingIndicator();
  
  try {
    const res = await fetch('/v1/chat', {
      method:'POST',
      headers:{'content-type':'application/json'},
      body: JSON.stringify({ messages: history })
    });

    removeTypingIndicator();

    if (!res.ok) {
      const errText = await res.text().catch(()=>res.statusText);
      const bubble = append('assistant', `⚠️ ${res.status} ${res.statusText}: ${errText}`, false);
      return;
    }

    const data = await res.json();
    const assistantMsg = data.message;
    const text = assistantMsg?.content || "⚠️ No response";

    append('assistant', text, false);
    history.push({ role:'assistant', content: text });
    saveSession();

    maybeTriggerCalendar(text);
    if (lastCaptureWasVoice) speakText(text);

  } catch (err) {
    removeTypingIndicator();
    append('assistant', `⚠️ Network error: ${err}`, false);
    console.error(err);
  }
}

/* --------------------------
   Handle text submit
--------------------------- */
async function handleSubmit(e){
  e.preventDefault();
  const text = $input.value.trim();
  if (!text) return;

  captureIdentity(text);
  append('user', text);
  history.push({ role: 'user', content: text });
  saveSession();

  $input.value = '';
  $input.focus();
  updateComposerMode();

  lastCaptureWasVoice = false;
  await sendChatOnce();
}

/* --------------------------
   ENHANCED Voice helpers with waveform
--------------------------- */

function initAudioVisualizer() {
  if (!$waveform || !window.AudioContext) return;
  
  audioContext = new (window.AudioContext || window.webkitAudioContext)();
  analyser = audioContext.createAnalyser();
  analyser.fftSize = 256;
}

function drawWaveform() {
  if (!analyser || !$waveform) return;
  
  const bufferLength = analyser.frequencyBinCount;
  const dataArray = new Uint8Array(bufferLength);
  analyser.getByteFrequencyData(dataArray);
  
  // Calculate average volume
  const average = dataArray.reduce((a, b) => a + b) / bufferLength;
  const scale = Math.min(average / 50, 3);
  
  $waveform.style.transform = `scaleX(${scale})`;
  
  if (isRecording) {
    animationId = requestAnimationFrame(drawWaveform);
  }
}

async function handleVoiceMessage(audioBlob){
  const fd = new FormData();
  fd.append('audio', audioBlob, 'speech.webm');
  let heardText = '', sttError = null;
  try {
    const sttRes = await fetch('/v1/stt', { method:'POST', body: fd });
    const sttData = await sttRes.json();
    heardText = sttData.text || '';
    sttError = sttData.error || null;
  } catch (err){ sttError = err.message || String(err); }

  if (!heardText) {
    finalizeListeningBubble(sttError ? "I couldn't transcribe that (STT error)." : "I couldn't hear anything.", true);
    console.warn("STT error detail:", sttError);
    return;
  }

  captureIdentity(heardText);
  finalizeListeningBubble(heardText, false);
  history.push({ role:'user', content: heardText });
  saveSession();

  lastCaptureWasVoice = true;
  await sendChatOnce();
}

/* --------------------------
   TTS playback
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
  }catch(err){ console.error("TTS failed", err); }
}

/* --------------------------
   Mic press+hold -> record with visualization
--------------------------- */
async function startRecording(){
  if(isRecording) return;
  isRecording = true;

  if ($mic)     $mic.classList.add('recording');
  if ($micWrap) $micWrap.classList.add('recording');

  showListeningBubble();

  micChunks = [];
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  
  // Setup audio visualization
  if (audioContext && analyser) {
    const source = audioContext.createMediaStreamSource(stream);
    source.connect(analyser);
    drawWaveform();
  }
  
  mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });

  mediaRecorder.ondataavailable = (e)=>{
    if(e.data.size > 0){ micChunks.push(e.data); }
  };

  mediaRecorder.start();
}

async function stopRecording(){
  if(!isRecording) return;
  isRecording = false;

  if ($mic)     $mic.classList.remove('recording');
  if ($micWrap) $micWrap.classList.remove('recording');
  
  if (animationId) {
    cancelAnimationFrame(animationId);
    animationId = null;
  }
  
  if ($waveform) {
    $waveform.style.transform = 'scaleX(1)';
  }

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
   Typing-mode toggle (ORIGINAL LOGIC PRESERVED)
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

/* --------------------------
   Wire up listeners for composer
--------------------------- */
function wireComposerEvents() {
  $form.onsubmit = null;
  $input.oninput = null;
  if ($mic) {
    $mic.onmousedown = null;
    $mic.ontouchstart = null;
  }
  window.onmouseup = null;
  window.ontouchend = null;

  $form.addEventListener('submit', handleSubmit);
  $input.addEventListener('input', updateComposerMode);
  updateComposerMode();

  // improved hold detection
  if ($mic) {
    let holdTimer;

    const handleHoldStart = (e) => {
      e.preventDefault();
      holdTimer = setTimeout(() => {
        if ($mic)     $mic.classList.add('recording');
        if ($micWrap) $micWrap.classList.add('recording');
        startRecording();
      }, 200);
    };

    const handleHoldEnd = () => {
      clearTimeout(holdTimer);
      stopRecording();
      if ($mic)     $mic.classList.remove('recording');
      if ($micWrap) $micWrap.classList.remove('recording');
    };

    $mic.addEventListener('mousedown', handleHoldStart);
    $mic.addEventListener('touchstart', handleHoldStart);
    window.addEventListener('mouseup', handleHoldEnd);
    window.addEventListener('touchend', handleHoldEnd);
  }
}

/* --------------------------
   Init
--------------------------- */
initSession();
initAudioVisualizer();
setupScrollManagement();
wireComposerEvents();