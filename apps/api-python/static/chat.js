/**
 * Mogul Assistant - Enhanced Chat Interface
 * Features:
 * - Sidebar with chat history
 * - Voice conversation mode (continuous flow)
 * - Orb animations
 * - Local storage for chat persistence
 */

// =====================================================
// DOM ELEMENTS
// =====================================================

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// Sidebar
const sidebar = $('#sidebar');
const sidebarOverlay = $('#sidebarOverlay');
const menuBtn = $('#menuBtn');
const newChatBtn = $('#newChatBtn');
const chatList = $('#chatList');

// Chat View
const welcomeScreen = $('#welcomeScreen');
const messagesContainer = $('#messagesContainer');
const messages = $('#messages');
const inputForm = $('#inputForm');
const messageInput = $('#messageInput');
const sendBtn = $('#sendBtn');
const micBtn = $('#micBtn');

// Voice Mode
const voiceModeBtn = $('#voiceModeBtn');
const voiceOverlay = $('#voiceOverlay');
const voiceCloseBtn = $('#voiceCloseBtn');
const voiceToggleBtn = $('#voiceToggleBtn');
const voiceStatus = $('#voiceStatus');
const voiceTranscript = $('#voiceTranscript');
const voiceOrb = $('#voiceOrb');

// Audio
const audioPlayer = $('#audioPlayer');

// Quick Actions
const quickActions = $$('.quick-action');

// =====================================================
// STATE
// =====================================================

let currentChatId = null;
let chatHistory = []; // Current chat messages
let allChats = {}; // All saved chats { id: { title, messages, updatedAt } }

// Voice state
let isVoiceMode = false;
let isListening = false;
let isSpeaking = false;
let mediaRecorder = null;
let audioChunks = [];
let silenceTimer = null;
let audioContext = null;
let analyser = null;

// Audio settings
let audioEnabled = true;
let audioUnlocked = false; // Track if we've unlocked audio playback

// Constants
const SILENCE_THRESHOLD = 1500; // ms of silence before processing
const STORAGE_KEY = 'mogul_chats';

// =====================================================
// INITIALIZATION
// =====================================================

function init() {
  loadChatsFromStorage();
  renderChatList();
  setupEventListeners();
  autoResizeTextarea();
  setupAudioPlayer();
  
  // Start fresh or load last chat
  const lastChatId = localStorage.getItem('mogul_current_chat');
  if (lastChatId && allChats[lastChatId]) {
    loadChat(lastChatId);
  }
  
  console.log('🚀 Mogul Assistant initialized');
}

// =====================================================
// AUDIO SETUP
// =====================================================

function setupAudioPlayer() {
  if (!audioPlayer) {
    console.error('❌ Audio player element not found!');
    return;
  }
  
  // Set audio properties
  audioPlayer.volume = 1.0;
  audioPlayer.preload = 'auto';
  
  // Debug events
  audioPlayer.addEventListener('play', () => {
    console.log('🔊 Audio started playing');
  });
  
  audioPlayer.addEventListener('ended', () => {
    console.log('🔊 Audio finished playing');
  });
  
  audioPlayer.addEventListener('error', (e) => {
    console.error('❌ Audio error:', e);
    console.error('Audio error code:', audioPlayer.error?.code);
    console.error('Audio error message:', audioPlayer.error?.message);
  });
  
  audioPlayer.addEventListener('canplay', () => {
    console.log('🔊 Audio can play');
  });
  
  console.log('✅ Audio player setup complete');
}

// Unlock audio on first user interaction (required by browsers)
function unlockAudio() {
  if (audioUnlocked) return;
  
  console.log('🔓 Attempting to unlock audio...');
  
  // Method 1: Play silent audio
  const silentAudio = new Audio();
  silentAudio.src = 'data:audio/mp3;base64,SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4Ljc2LjEwMAAAAAAAAAAAAAAA//tQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWGluZwAAAA8AAAACAAABhgC7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7//////////////////////////////////////////////////////////////////8AAAAATGF2YzU4LjEzAAAAAAAAAAAAAAAAJAAAAAAAAAAAAYYoRwmHAAAAAAD/+xBEAA/wAABpAAAACAAADSAAAAEAAAGkAAAAIAAANIAAAARMQU1FMy4xMDBVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVX/+xBEHw/wAABpAAAACAAADSAAAAEAAAGkAAAAIAAANIAAAARVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV';
  silentAudio.volume = 0.1;
  
  const playPromise = silentAudio.play();
  if (playPromise !== undefined) {
    playPromise
      .then(() => {
        audioUnlocked = true;
        console.log('🔓 ✅ Audio playback unlocked via silent audio');
        silentAudio.pause();
      })
      .catch(err => {
        console.log('🔓 Silent audio method failed:', err.message);
      });
  }
  
  // Method 2: Create and resume AudioContext
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === 'suspended') {
      ctx.resume().then(() => {
        audioUnlocked = true;
        console.log('🔓 ✅ Audio unlocked via AudioContext resume');
      });
    } else {
      audioUnlocked = true;
      console.log('🔓 ✅ AudioContext already running');
    }
  } catch (e) {
    console.log('🔓 AudioContext method failed:', e.message);
  }
}

// Test audio function - call this from console: testAudio()
window.testAudio = async function() {
  console.log('🧪 Testing audio playback...');
  
  try {
    // Test 1: Basic Audio element
    console.log('🧪 Test 1: Basic Audio element...');
    const audio1 = new Audio();
    audio1.src = 'data:audio/wav;base64,UklGRnoGAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQoGAACBhYqFbF1fdJivrJBhNjVgodDbq2EcBj+a2teleqVOEZ+kq8k0HWVIU1GQkJBnRz07YZS8yeOdQhl+vdDw7Kt1NxFXrtbr5LF1MgtWq87q47l+NhFbr9ft6LmBOBNhtebx7r+FPRdptOz39saNRB1wu/T++tCTSiF2wfv//taaUSh9xv//+t+hWS+Fy///+OsncDqL0P//9/AudT+Q1f//9vMyfUCV2f//9fYzgESY3f//9PszhUic4f//8/80ik2e5P//8/81j1Gg5///8v82lFaj6f//8f84mVym7P//8P85nWCo7///7/86oGSr8v//7v88o2iu9P//7P88p2yw9///6/89qnC0+f//6v8+rnS3+///6P8/snm6/f//5/9Au32+/v//5f9Cv4HC//';
    audio1.volume = 1.0;
    await audio1.play();
    console.log('🧪 ✅ Test 1 passed: Basic Audio works');
    audio1.pause();
  } catch (e) {
    console.error('🧪 ❌ Test 1 failed:', e.message);
  }
  
  try {
    // Test 2: Fetch and play TTS
    console.log('🧪 Test 2: Fetching TTS audio...');
    const response = await fetch('/v1/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: 'Hello! This is a test.' })
    });
    
    if (!response.ok) {
      throw new Error('TTS request failed');
    }
    
    const blob = await response.blob();
    console.log('🧪 Got blob:', blob.size, 'bytes');
    
    const url = URL.createObjectURL(blob);
    const audio2 = new Audio(url);
    audio2.volume = 1.0;
    
    await new Promise((resolve, reject) => {
      audio2.onended = () => {
        console.log('🧪 ✅ Test 2 passed: TTS audio played successfully!');
        resolve();
      };
      audio2.onerror = (e) => {
        console.error('🧪 ❌ Test 2 error:', e);
        reject(e);
      };
      audio2.play().catch(reject);
    });
    
    URL.revokeObjectURL(url);
    
  } catch (e) {
    console.error('🧪 ❌ Test 2 failed:', e.message);
  }
  
  console.log('🧪 Audio test complete. Check your speakers!');
};

// =====================================================
// STORAGE
// =====================================================

function loadChatsFromStorage() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      allChats = JSON.parse(saved);
    }
  } catch (e) {
    console.error('Failed to load chats:', e);
    allChats = {};
  }
}

function saveChatsToStorage() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(allChats));
    if (currentChatId) {
      localStorage.setItem('mogul_current_chat', currentChatId);
    }
  } catch (e) {
    console.error('Failed to save chats:', e);
  }
}

function generateChatId() {
  return 'chat_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
}

function getChatTitle(messages) {
  const userMsg = messages.find(m => m.role === 'user');
  if (userMsg && userMsg.content) {
    return userMsg.content.slice(0, 40) + (userMsg.content.length > 40 ? '...' : '');
  }
  return 'New conversation';
}

// =====================================================
// CHAT MANAGEMENT
// =====================================================

function startNewChat() {
  // Save current chat if exists
  if (currentChatId && chatHistory.length > 0) {
    saveCurrentChat();
  }
  
  // Reset state
  currentChatId = generateChatId();
  chatHistory = [];
  
  // Reset UI
  messages.innerHTML = '';
  welcomeScreen.classList.remove('hidden');
  messagesContainer.classList.remove('active');
  messageInput.value = '';
  updateSendButton();
  
  // Close sidebar on mobile
  closeSidebar();
  
  saveChatsToStorage();
  renderChatList();
}

function saveCurrentChat() {
  if (!currentChatId || chatHistory.length === 0) return;
  
  allChats[currentChatId] = {
    id: currentChatId,
    title: getChatTitle(chatHistory),
    messages: chatHistory,
    updatedAt: Date.now()
  };
  
  saveChatsToStorage();
  renderChatList();
}

function loadChat(chatId) {
  // Save current first
  if (currentChatId && chatHistory.length > 0) {
    saveCurrentChat();
  }
  
  const chat = allChats[chatId];
  if (!chat) return;
  
  currentChatId = chatId;
  chatHistory = [...chat.messages];
  
  // Render messages
  messages.innerHTML = '';
  chatHistory.forEach(msg => {
    if (msg.role === 'user' || msg.role === 'assistant') {
      appendMessage(msg.role, msg.content, false);
    }
  });
  
  // Show chat view
  welcomeScreen.classList.add('hidden');
  messagesContainer.classList.add('active');
  
  // Mark as active in sidebar
  $$('.chat-item').forEach(el => el.classList.remove('active'));
  const activeItem = $(`.chat-item[data-id="${chatId}"]`);
  if (activeItem) activeItem.classList.add('active');
  
  closeSidebar();
  scrollToBottom();
}

function deleteChat(chatId) {
  delete allChats[chatId];
  
  if (currentChatId === chatId) {
    startNewChat();
  }
  
  saveChatsToStorage();
  renderChatList();
}

function renderChatList() {
  const sortedChats = Object.values(allChats)
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, 20); // Show last 20 chats
  
  if (sortedChats.length === 0) {
    chatList.innerHTML = `
      <div class="chat-item" style="color: var(--text-muted); pointer-events: none;">
        <span>No previous chats</span>
      </div>
    `;
    return;
  }
  
  chatList.innerHTML = sortedChats.map(chat => `
    <div class="chat-item ${chat.id === currentChatId ? 'active' : ''}" data-id="${chat.id}">
      <div class="chat-item-icon">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
      </div>
      <div class="chat-item-content">
        <div class="chat-item-title">${escapeHtml(chat.title)}</div>
        <div class="chat-item-time">${formatTime(chat.updatedAt)}</div>
      </div>
    </div>
  `).join('');
  
  // Add click handlers
  $$('.chat-item[data-id]').forEach(el => {
    el.addEventListener('click', () => loadChat(el.dataset.id));
  });
}

// =====================================================
// MESSAGE HANDLING
// =====================================================

function appendMessage(role, content, animate = true) {
  const avatar = role === 'assistant' ? 'M' : 'You';
  const bubble = document.createElement('div');
  bubble.className = `message ${role}`;
  
  bubble.innerHTML = `
    <div class="message-avatar">${avatar}</div>
    <div class="message-content">
      <div class="message-bubble">${linkify(content)}</div>
    </div>
  `;
  
  if (!animate) {
    bubble.style.animation = 'none';
  }
  
  messages.appendChild(bubble);
  scrollToBottom();
  
  return bubble;
}

function appendThinking() {
  const bubble = document.createElement('div');
  bubble.className = 'message assistant';
  bubble.id = 'thinking-bubble';
  
  bubble.innerHTML = `
    <div class="message-avatar">M</div>
    <div class="message-content">
      <div class="message-bubble thinking">
        <div class="typing-indicator">
          <span></span>
          <span></span>
          <span></span>
        </div>
      </div>
    </div>
  `;
  
  messages.appendChild(bubble);
  scrollToBottom();
  
  return bubble;
}

function removeThinking() {
  const bubble = $('#thinking-bubble');
  if (bubble) bubble.remove();
}

function scrollToBottom() {
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

// =====================================================
// SEND MESSAGE
// =====================================================

async function sendMessage(text, fromVoice = false) {
  if (!text.trim()) return;
  
  // Unlock audio on user interaction
  unlockAudio();
  
  // Hide welcome, show chat
  welcomeScreen.classList.add('hidden');
  messagesContainer.classList.add('active');
  
  // Add user message
  chatHistory.push({ role: 'user', content: text });
  appendMessage('user', text);
  
  // Clear input
  messageInput.value = '';
  updateSendButton();
  
  // Show thinking
  appendThinking();
  
  try {
    const response = await fetch('/v1/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: chatHistory })
    });
    
    const data = await response.json();
    const assistantContent = data.message?.content || 'Sorry, I encountered an error.';
    
    // Remove thinking, add response
    removeThinking();
    chatHistory.push({ role: 'assistant', content: assistantContent });
    appendMessage('assistant', assistantContent);
    
    // Save chat
    saveCurrentChat();
    
    // If from voice mode OR in voice overlay, speak the response
    if (fromVoice || isVoiceMode) {
      console.log('🎤 Speaking response (voice mode)');
      await speakText(assistantContent);
    }
    
  } catch (error) {
    console.error('Chat error:', error);
    removeThinking();
    appendMessage('assistant', 'Sorry, something went wrong. Please try again.');
  }
}

// =====================================================
// TEXT TO SPEECH
// =====================================================

// Create a dedicated audio element for TTS playback
let ttsAudio = null;

function createTTSAudio() {
  if (ttsAudio) {
    ttsAudio.pause();
    ttsAudio.src = '';
  }
  
  ttsAudio = new Audio();
  ttsAudio.volume = 1.0;
  ttsAudio.preload = 'auto';
  
  // Important: Allow audio to play
  ttsAudio.setAttribute('playsinline', '');
  ttsAudio.setAttribute('webkit-playsinline', '');
  
  return ttsAudio;
}

async function speakText(text) {
  if (!text) {
    console.error('❌ Cannot speak: no text');
    return;
  }
  
  console.log('🔊 Starting TTS for:', text.substring(0, 50) + '...');
  
  isSpeaking = true;
  updateVoiceState('speaking');
  
  try {
    // Fetch audio from TTS endpoint
    const response = await fetch('/v1/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text })
    });
    
    if (!response.ok) {
      throw new Error(`TTS request failed: ${response.status}`);
    }
    
    const audioBlob = await response.blob();
    console.log('🔊 Received audio blob:', audioBlob.size, 'bytes, type:', audioBlob.type);
    
    if (audioBlob.size === 0) {
      throw new Error('Received empty audio blob');
    }
    
    // Create a proper blob with correct MIME type
    const mp3Blob = new Blob([audioBlob], { type: 'audio/mpeg' });
    const audioUrl = URL.createObjectURL(mp3Blob);
    console.log('🔊 Created audio URL');
    
    // Create fresh audio element each time
    const audio = createTTSAudio();
    
    // Play the audio
    await new Promise((resolve, reject) => {
      const cleanup = () => {
        audio.removeEventListener('ended', onEnded);
        audio.removeEventListener('error', onError);
        audio.removeEventListener('canplaythrough', onCanPlay);
        URL.revokeObjectURL(audioUrl);
      };
      
      const onEnded = () => {
        console.log('🔊 Audio playback completed');
        cleanup();
        resolve();
      };
      
      const onError = (e) => {
        console.error('❌ Audio error:', audio.error);
        cleanup();
        reject(new Error('Audio playback failed'));
      };
      
      const onCanPlay = () => {
        console.log('🔊 Audio can play, starting...');
        audio.play()
          .then(() => {
            console.log('🔊 ✅ Audio is now playing!');
          })
          .catch(err => {
            console.error('❌ Play failed:', err);
            // Try alternative method
            tryAlternativePlay(audioUrl, resolve, reject);
          });
      };
      
      audio.addEventListener('ended', onEnded);
      audio.addEventListener('error', onError);
      audio.addEventListener('canplaythrough', onCanPlay);
      
      // Set source and load
      audio.src = audioUrl;
      audio.load();
      
      // Timeout fallback
      setTimeout(() => {
        if (isSpeaking) {
          console.log('🔊 Playback timeout, resolving...');
          cleanup();
          resolve();
        }
      }, 30000); // 30 second timeout for long responses
    });
    
  } catch (error) {
    console.error('❌ TTS error:', error);
  } finally {
    isSpeaking = false;
    
    // If still in voice mode, ready for next input
    if (isVoiceMode) {
      updateVoiceState('idle');
      voiceStatus.textContent = 'Tap to continue speaking';
    }
  }
}

// Alternative play method using AudioContext (fallback)
async function tryAlternativePlay(audioUrl, resolve, reject) {
  console.log('🔊 Trying alternative AudioContext playback...');
  
  try {
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    
    // Resume context if suspended (needed for some browsers)
    if (audioCtx.state === 'suspended') {
      await audioCtx.resume();
    }
    
    const response = await fetch(audioUrl);
    const arrayBuffer = await response.arrayBuffer();
    const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
    
    const source = audioCtx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(audioCtx.destination);
    
    source.onended = () => {
      console.log('🔊 AudioContext playback completed');
      audioCtx.close();
      resolve();
    };
    
    source.start(0);
    console.log('🔊 ✅ AudioContext playback started!');
    
  } catch (err) {
    console.error('❌ Alternative playback also failed:', err);
    reject(err);
  }
}

// =====================================================
// VOICE MODE
// =====================================================

function openVoiceMode() {
  isVoiceMode = true;
  voiceOverlay.classList.add('active');
  voiceStatus.textContent = 'Tap to start speaking';
  voiceTranscript.textContent = '';
  updateVoiceState('idle');
  
  // Unlock audio when entering voice mode
  unlockAudio();
  
  console.log('🎤 Voice mode opened');
}

function closeVoiceMode() {
  isVoiceMode = false;
  voiceOverlay.classList.remove('active');
  stopListening();
  stopSpeaking();
  
  console.log('🎤 Voice mode closed');
}

function updateVoiceState(state) {
  voiceOverlay.classList.remove('listening', 'speaking', 'processing');
  
  switch (state) {
    case 'listening':
      voiceOverlay.classList.add('listening');
      voiceStatus.textContent = 'Listening...';
      break;
    case 'processing':
      voiceOverlay.classList.add('processing');
      voiceStatus.textContent = 'Processing...';
      break;
    case 'speaking':
      voiceOverlay.classList.add('speaking');
      voiceStatus.textContent = 'Speaking...';
      break;
    default:
      voiceStatus.textContent = 'Tap to start speaking';
  }
}

async function toggleVoice() {
  // Unlock audio on interaction
  unlockAudio();
  
  if (isListening) {
    stopListening();
  } else if (isSpeaking) {
    stopSpeaking();
    // After stopping speech, start listening again
    setTimeout(() => startListening(), 300);
  } else {
    startListening();
  }
}

async function startListening() {
  if (isListening) return;
  
  console.log('🎤 Starting to listen...');
  
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    
    isListening = true;
    audioChunks = [];
    updateVoiceState('listening');
    voiceTranscript.textContent = '';
    
    // Setup audio analysis for silence detection
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    analyser = audioContext.createAnalyser();
    const source = audioContext.createMediaStreamSource(stream);
    source.connect(analyser);
    analyser.fftSize = 256;
    
    // Setup MediaRecorder
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') 
      ? 'audio/webm;codecs=opus' 
      : 'audio/webm';
    
    mediaRecorder = new MediaRecorder(stream, { mimeType });
    
    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) {
        audioChunks.push(e.data);
      }
    };
    
    mediaRecorder.onstop = async () => {
      console.log('🎤 Recording stopped, chunks:', audioChunks.length);
      stream.getTracks().forEach(track => track.stop());
      
      if (audioChunks.length > 0) {
        const audioBlob = new Blob(audioChunks, { type: mimeType });
        console.log('🎤 Audio blob size:', audioBlob.size);
        await processVoiceInput(audioBlob);
      }
    };
    
    mediaRecorder.start(100); // Collect data every 100ms
    console.log('🎤 MediaRecorder started');
    
    // Start silence detection
    detectSilence();
    
  } catch (error) {
    console.error('❌ Microphone error:', error);
    voiceStatus.textContent = 'Microphone access denied';
    isListening = false;
  }
}

function detectSilence() {
  if (!isListening || !analyser) return;
  
  const dataArray = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteFrequencyData(dataArray);
  
  const average = dataArray.reduce((a, b) => a + b) / dataArray.length;
  
  if (average < 10) {
    // Silence detected
    if (!silenceTimer) {
      silenceTimer = setTimeout(() => {
        if (isListening && audioChunks.length > 0) {
          console.log('🎤 Silence detected, stopping...');
          stopListening();
        }
      }, SILENCE_THRESHOLD);
    }
  } else {
    // Sound detected, reset timer
    if (silenceTimer) {
      clearTimeout(silenceTimer);
      silenceTimer = null;
    }
  }
  
  if (isListening) {
    requestAnimationFrame(detectSilence);
  }
}

function stopListening() {
  if (!isListening) return;
  
  console.log('🎤 Stopping listening...');
  isListening = false;
  
  if (silenceTimer) {
    clearTimeout(silenceTimer);
    silenceTimer = null;
  }
  
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
  
  if (audioContext) {
    audioContext.close();
    audioContext = null;
  }
  
  updateVoiceState('processing');
}

async function processVoiceInput(audioBlob) {
  console.log('🎤 Processing voice input...');
  
  try {
    const formData = new FormData();
    formData.append('audio', audioBlob, 'speech.webm');
    
    const response = await fetch('/v1/stt', {
      method: 'POST',
      body: formData
    });
    
    const data = await response.json();
    const transcript = data.text;
    
    console.log('🎤 Transcript:', transcript);
    
    if (transcript && transcript.trim()) {
      voiceTranscript.textContent = `"${transcript}"`;
      await sendMessage(transcript, true);
    } else {
      voiceStatus.textContent = "I didn't catch that. Tap to try again.";
      updateVoiceState('idle');
    }
    
  } catch (error) {
    console.error('❌ STT error:', error);
    voiceStatus.textContent = 'Error processing voice. Tap to try again.';
    updateVoiceState('idle');
  }
}

function stopSpeaking() {
  if (!isSpeaking) return;
  
  console.log('🔊 Stopping speech...');
  
  // Stop the TTS audio
  if (ttsAudio) {
    ttsAudio.pause();
    ttsAudio.currentTime = 0;
  }
  
  // Also stop the audioPlayer element if it exists
  if (audioPlayer) {
    audioPlayer.pause();
    audioPlayer.currentTime = 0;
  }
  
  isSpeaking = false;
}

// =====================================================
// SIDEBAR
// =====================================================

function openSidebar() {
  sidebar.classList.add('open');
  sidebarOverlay.classList.add('active');
}

function closeSidebar() {
  sidebar.classList.remove('open');
  sidebarOverlay.classList.remove('active');
}

function toggleSidebar() {
  if (sidebar.classList.contains('open')) {
    closeSidebar();
  } else {
    openSidebar();
  }
}

// =====================================================
// UTILITIES
// =====================================================

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function linkify(text) {
  const urlRegex = /(https?:\/\/[^\s]+)/g;
  return escapeHtml(text).replace(urlRegex, '<a href="$1" target="_blank" rel="noopener">$1</a>');
}

function formatTime(timestamp) {
  const date = new Date(timestamp);
  const now = new Date();
  const diff = now - date;
  
  if (diff < 60000) return 'Just now';
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  if (diff < 604800000) return `${Math.floor(diff / 86400000)}d ago`;
  
  return date.toLocaleDateString();
}

function updateSendButton() {
  const hasText = messageInput.value.trim().length > 0;
  sendBtn.disabled = !hasText;
  
  // Show/hide mic vs send
  if (hasText) {
    micBtn.style.display = 'none';
    sendBtn.style.display = 'flex';
  } else {
    micBtn.style.display = 'flex';
    sendBtn.style.display = 'none';
  }
}

function autoResizeTextarea() {
  messageInput.addEventListener('input', () => {
    messageInput.style.height = 'auto';
    messageInput.style.height = Math.min(messageInput.scrollHeight, 200) + 'px';
  });
}

// =====================================================
// EVENT LISTENERS
// =====================================================

function setupEventListeners() {
  // Unlock audio on any click/touch
  document.addEventListener('click', unlockAudio, { once: true });
  document.addEventListener('touchstart', unlockAudio, { once: true });
  
  // Sidebar
  menuBtn?.addEventListener('click', toggleSidebar);
  sidebarOverlay?.addEventListener('click', closeSidebar);
  newChatBtn?.addEventListener('click', startNewChat);
  
  // Form submission
  inputForm.addEventListener('submit', (e) => {
    e.preventDefault();
    sendMessage(messageInput.value);
  });
  
  // Input changes
  messageInput.addEventListener('input', updateSendButton);
  
  // Quick text mic button (for quick voice input without full mode)
  micBtn?.addEventListener('click', () => {
    openVoiceMode();
  });
  
  // Voice mode
  voiceModeBtn?.addEventListener('click', openVoiceMode);
  voiceCloseBtn?.addEventListener('click', closeVoiceMode);
  voiceToggleBtn?.addEventListener('click', toggleVoice);
  
  // Quick actions
  quickActions.forEach(btn => {
    btn.addEventListener('click', () => {
      const prompt = btn.dataset.prompt;
      if (prompt) {
        sendMessage(prompt);
      }
    });
  });
  
  // Keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    // Escape to close voice mode
    if (e.key === 'Escape' && isVoiceMode) {
      closeVoiceMode();
    }
    
    // Cmd/Ctrl + K for new chat
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      startNewChat();
    }
  });
  
  // Handle Enter key in textarea
  messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (messageInput.value.trim()) {
        sendMessage(messageInput.value);
      }
    }
  });
}

// =====================================================
// INITIALIZE
// =====================================================

init();