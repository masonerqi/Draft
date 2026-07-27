const state = {
  recognition: null,
  liveTranscript: "",
  isRecording: false,
  userRequestedStop: false,
  activeSessionId: null,
  lastInputWasAudio: false,
  currentUser: null,
  timerInterval: null,
  totalSeconds: 0,
};

const elements = {
  authView: document.getElementById("auth-view"),
  appShell: document.getElementById("app-shell"),
  authError: document.getElementById("auth-error"),
  authUsername: document.getElementById("auth-username"),
  authPassword: document.getElementById("auth-password"),
  authSubmit: document.getElementById("auth-submit"),
  loginTab: document.getElementById("login-tab"),
  registerTab: document.getElementById("register-tab"),
  profileName: document.getElementById("profile-name"),
  profileEmail: document.getElementById("profile-email"),
  profileInitials: document.getElementById("profile-initials"),
  historyList: document.getElementById("history-list"),
  recordButton: document.getElementById("dock-record-btn"),
  copyButton: document.getElementById("copy-btn"),
  settingsButton: document.getElementById("settings-btn"),
  logoutButton: document.getElementById("logout-btn"),
  inputView: document.getElementById("input-view"),
  resultsView: document.getElementById("results"),
  settingsModal: document.getElementById("settings-modal"),
  settingsError: document.getElementById("settings-error"),
  settingsStatus: document.getElementById("settings-status"),
  apiKeyInput: document.getElementById("api-key-input"),
  loadingOverlay: document.getElementById("loading"),
  loadingMsg: document.getElementById("loading-msg"),
  errorBanner: document.getElementById("error-banner"),
  resultsSummary: document.getElementById("result-summary"),
  resultMeta: document.getElementById("result-meta"),
  resultDecisions: document.getElementById("result-decisions"),
  resultActions: document.getElementById("result-actions"),
  transcriptSection: document.getElementById("transcript-section"),
  transcriptText: document.getElementById("transcript-text"),
  transcriptInput: document.getElementById("transcript-input"),
  exportButton: document.getElementById("exportTextBtn"),
  newSessionButton: document.getElementById("new-session-btn"),
  submitAuthButton: document.getElementById("auth-submit"),
  authTabs: document.querySelectorAll(".auth-tab"),
};

function renderLucideIcons() {
  if (window.lucide && typeof lucide.createIcons === "function") {
    lucide.createIcons();
  }
}

function formatInitials(name) {
  if (!name) return "D";
  return name
    .split(" ")
    .map((part) => part.charAt(0).toUpperCase())
    .slice(0, 2)
    .join("");
}

async function fetchCurrentUser() {
  try {
    const response = await fetch("/me", { credentials: "same-origin" });
    if (!response.ok) {
      return null;
    }
    return await response.json();
  } catch (error) {
    console.warn("Unable to fetch current user:", error);
    return null;
  }
}

function showAuthMode(mode) {
  state.authMode = mode;
  elements.loginTab.classList.toggle("active", mode === "login");
  elements.registerTab.classList.toggle("active", mode === "register");
  elements.authSubmit.textContent = mode === "login" ? "Login" : "Register";
  elements.authError.textContent = "";
}

function showAuthView(message = "") {
  elements.authView.classList.remove("hidden");
  elements.appShell.classList.add("hidden");
  elements.authError.textContent = message;
}

async function showAppView(user) {
  state.currentUser = user;
  elements.authView.classList.add("hidden");
  elements.appShell.classList.remove("hidden");
  elements.profileName.textContent = user.username;
  elements.profileEmail.textContent = user.username;
  elements.profileInitials.textContent = formatInitials(user.username);
  await loadHistory();
  await ensureApiKeySaved();
}

async function submitAuth() {
  const username = elements.authUsername.value.trim();
  const password = elements.authPassword.value.trim();
  if (!username || !password) {
    elements.authError.textContent = "Both username and password are required.";
    return;
  }

  const endpoint = state.authMode === "login" ? "/login" : "/register";
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ username, password }),
    });
    const data = await response.json();
    if (!response.ok) {
      elements.authError.textContent = data.error || "Authentication failed.";
      return;
    }
    await showAppView({ id: data.id, username: data.username });
  } catch (error) {
    elements.authError.textContent = "Unable to communicate with the server.";
  }
}

async function logout() {
  try {
    await fetch("/logout", { method: "POST", credentials: "same-origin" });
  } catch (error) {
    console.warn("Logout failed", error);
  }
  state.currentUser = null;
  showAuthView();
}

function startTimer() {
  clearInterval(state.timerInterval);
  state.totalSeconds = 0;
  state.timerInterval = setInterval(() => {
    state.totalSeconds += 1;
    const minutes = Math.floor(state.totalSeconds / 60);
    const seconds = state.totalSeconds % 60;
    const formattedTime = `${minutes}:${seconds.toString().padStart(2, "0")}`;
    document.getElementById("dock-timer-display").textContent = formattedTime;
  }, 1000);
}

function stopTimer() {
  clearInterval(state.timerInterval);
}

function updateRecordingStatusUI(recording, text) {
  const statusTextIndicator = document.getElementById("record-status-text");
  const indicatorComponent = document.getElementById("record-status-indicator");
  const dockButton = document.getElementById("dock-record-btn");

  statusTextIndicator.textContent = text;
  if (recording) {
    indicatorComponent.classList.add("active");
    dockButton.classList.add("recording");
  } else {
    indicatorComponent.classList.remove("active");
    dockButton.classList.remove("recording");
  }
}

function stopRecordingIfActive() {
  if (state.isRecording && state.recognition) {
    state.userRequestedStop = true;
    state.recognition.stop();
  }
}

async function toggleRecord() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    updateRecordingStatusUI(false, "Unsupported Browser");
    alert("Live speech recognition is not supported by this browser. Switch to Chrome or Edge.");
    return;
  }

  if (!state.isRecording) {
    try {
      await navigator.mediaDevices.getUserMedia({ audio: true });
      state.recognition = new SpeechRecognition();
      state.recognition.continuous = true;
      state.recognition.interimResults = true;
      state.recognition.lang = "en-US";

      const textInput = elements.transcriptInput;
      state.liveTranscript = textInput.value ? textInput.value + "\n" : "";
      state.userRequestedStop = false;
      state.lastInputWasAudio = true;

      state.recognition.onstart = () => {
        state.isRecording = true;
        updateRecordingStatusUI(true, "Recording...");
        document.getElementById("live-badge").style.display = "inline-flex";
        startTimer();
      };

      state.recognition.onresult = (event) => {
        let interimTranscript = "";
        for (let i = event.resultIndex; i < event.results.length; ++i) {
          if (event.results[i].isFinal) {
            state.liveTranscript += event.results[i][0].transcript + " ";
          } else {
            interimTranscript += event.results[i][0].transcript;
          }
        }
        textInput.value = state.liveTranscript + interimTranscript;
        textInput.scrollTop = textInput.scrollHeight;
      };

      state.recognition.onerror = (err) => {
        console.error("Speech Error Log:", err.error);
        if (err.error !== "no-speech") {
          updateRecordingStatusUI(state.isRecording, "Mic error: " + err.error);
        }
      };

      state.recognition.onend = () => {
        if (!state.userRequestedStop) {
          try {
            state.recognition.start();
          } catch (e) {
            setTimeout(() => {
              if (!state.userRequestedStop) state.recognition.start();
            }, 250);
          }
          return;
        }
        state.isRecording = false;
        updateRecordingStatusUI(false, "Stopping...");
        document.getElementById("live-badge").classList.add("hidden");
        setTimeout(() => updateRecordingStatusUI(false, "Captured"), 1500);
        stopTimer();
      };

      state.recognition.start();
    } catch (err) {
      updateRecordingStatusUI(false, "Permissions Denied");
    }
  } else {
    state.userRequestedStop = true;
    if (state.recognition) {
      state.recognition.stop();
    }
  }
}

function copyTranscriptToClipboard() {
  const text = elements.transcriptInput.value;
  navigator.clipboard.writeText(text).then(() => {
    alert("Transcript copied to clipboard.");
  });
}

async function submitTranscript() {
  stopRecordingIfActive();
  const text = elements.transcriptInput.value.trim();
  if (!text) {
    alert("Please compile or capture a live transcript sequence first.");
    return;
  }
  const formData = new FormData();
  formData.append("transcript", text);
  formData.append("source_hint", state.lastInputWasAudio ? "voice" : "manual");
  await submitToAPI(formData, "Sending content to Gemini Engine...");
}

async function submitToAPI(formData, msg) {
  showLoading(msg);
  hideError();
  try {
    const res = await fetch("/summarise", {
      method: "POST",
      body: formData,
      credentials: "same-origin",
    });
    const data = await res.json();
    if (!res.ok) {
      const errorMessage = data.error || "Unknown server fault";
      if (errorMessage.toLowerCase().includes("no gemini api key configured") || errorMessage.toLowerCase().includes("gemini api key is required")) {
        openSettings();
        elements.settingsError.textContent = errorMessage;
        hideLoading();
        return;
      }
      alert("Error: " + errorMessage);
      hideLoading();
      showInput();
      return;
    }
    showResults(data);
    loadHistory();
  } catch (err) {
    alert("Network exception. Confirm your Flask container or server is running.");
    hideLoading();
    showInput();
  }
}

function showResults(data) {
  hideLoading();
  state.activeSessionId = data.session_id || data.id || null;
  elements.resultsSummary.textContent = data.summary || "No summary available.";

  const meta = [];
  if (data.input_type) {
    meta.push(data.input_type === "audio" ? "Voice Capture" : "Pasted Text");
  }
  if (data.created_at) {
    meta.push(new Date(data.created_at).toLocaleString());
  }
  elements.resultMeta.textContent = meta.join(" · ");

  elements.resultDecisions.innerHTML = "";
  const decisions = Array.isArray(data.decisions) ? data.decisions : [];
  if (decisions.length === 0) {
    elements.resultDecisions.innerHTML = "<li>No concrete structural decisions resolved.</li>";
  } else {
    decisions.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      elements.resultDecisions.appendChild(li);
    });
  }

  elements.resultActions.innerHTML = "";
  const actions = Array.isArray(data.action_items) ? data.action_items : [];
  if (actions.length === 0) {
    elements.resultActions.innerHTML = "<li>No pending contextual action items.</li>";
  } else {
    actions.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      elements.resultActions.appendChild(li);
    });
  }

  if (data.transcript && data.transcript.trim()) {
    elements.transcriptSection.style.display = "block";
    elements.transcriptText.textContent = data.transcript;
    document.getElementById("transcript-toggle").classList.remove("open");
  } else {
    elements.transcriptSection.style.display = "none";
  }

  elements.inputView.classList.add("hidden");
  elements.resultsView.classList.remove("hidden");
  document.getElementById("floating-dock-row").style.display = "none";
  highlightActiveHistoryItem();
}

function toggleTranscript() {
  document.getElementById("transcript-toggle").classList.toggle("open");
}

function showError(message) {
  elements.errorBanner.textContent = message;
  elements.errorBanner.style.display = "block";
  elements.inputView.classList.add("hidden");
  elements.loadingOverlay.classList.add("hidden");
  elements.resultsView.classList.remove("hidden");
}

function hideError() {
  elements.errorBanner.style.display = "none";
}

async function loadHistory() {
  try {
    const res = await fetch("/sessions", { credentials: "same-origin" });
    if (res.status === 401) {
      logout();
      return;
    }
    const data = await res.json();
    if (!Array.isArray(data) || data.length === 0) {
      elements.historyList.innerHTML = '<li class="history-empty">No sessions yet.</li>';
      renderLucideIcons();
      return;
    }

    elements.historyList.innerHTML = data
      .map((session) => `
        <li class="history-item" data-id="${session.id}">
          <div class="history-item-body" onclick="loadSession(${session.id})">
            <div class="history-title">${escapeHtml(session.summary || "Untitled summary").substring(0, 55)}${(session.summary || "").length > 55 ? "..." : ""}</div>
            <div class="history-meta">${new Date(session.created_at).toLocaleDateString()}</div>
          </div>
          <button type="button" aria-label="Delete session" onclick="event.stopPropagation(); deleteSession(${session.id})">
            <i data-lucide="trash-2"></i>
          </button>
        </li>
      `)
      .join("");

    renderLucideIcons();
    highlightActiveHistoryItem();
  } catch (e) {
    console.warn("History unreachable.", e);
  }
}

function highlightActiveHistoryItem() {
  document.querySelectorAll(".history-item").forEach((el) => {
    el.classList.toggle("active", String(state.activeSessionId) === el.dataset.id);
  });
}

window.loadSession = async function (id) {
  try {
    const res = await fetch(`/sessions/${id}`, { credentials: "same-origin" });
    if (!res.ok) {
      showError("This session couldn't be loaded. It may have been removed.");
      return;
    }
    const data = await res.json();
    showResults(data);
  } catch (err) {
    console.error("Session load exception:", err);
    showError("This session couldn't be loaded. It may be corrupted.");
  }
};

window.deleteSession = async function (id) {
  await fetch(`/sessions/${id}`, { method: "DELETE", credentials: "same-origin" });
  if (state.activeSessionId === id) resetToInput();
  loadHistory();
};

async function openSettings() {
  elements.settingsError.textContent = "";
  elements.settingsStatus.textContent = "";
  elements.apiKeyInput.value = "";
  elements.settingsModal.classList.remove("hidden");
  await loadUserSettings();
  renderLucideIcons();
}

function closeSettings() {
  elements.settingsModal.classList.add("hidden");
  if (state.activeSessionId) {
    elements.resultsView.classList.remove("hidden");
  } else {
    elements.inputView.classList.remove("hidden");
  }
}

async function loadUserSettings() {
  try {
    const res = await fetch("/api/user/settings", { credentials: "same-origin" });
    if (!res.ok) {
      throw new Error("Unable to load settings.");
    }
    const data = await res.json();
    elements.settingsStatus.textContent = data.api_key_saved
      ? `Saved key: ${data.api_key_masked}`
      : "No API key saved yet. Paste your Google AI Studio Gemini key above.";
    return data;
  } catch (error) {
    elements.settingsError.textContent = error.message;
    return null;
  }
}

async function ensureApiKeySaved() {
  const data = await loadUserSettings();
  if (data && !data.api_key_saved) {
    openSettings();
    elements.settingsStatus.textContent = "Please paste your Gemini API key to continue.";
  }
}

async function saveApiKey() {
  const apiKey = elements.apiKeyInput.value.trim();
  elements.settingsError.textContent = "";
  if (!apiKey) {
    elements.settingsError.textContent = "Please paste your Gemini API key.";
    return;
  }

  try {
    const res = await fetch("/api/user/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ gemini_api_key: apiKey }),
    });
    const data = await res.json();
    if (!res.ok) {
      elements.settingsError.textContent = data.error || "Failed to save API key.";
      return;
    }
    elements.settingsStatus.textContent = `Saved key: ${data.api_key_masked}`;
    elements.apiKeyInput.value = "";
    closeSettings();
  } catch (error) {
    elements.settingsError.textContent = error.message || "Unable to save API key.";
  }
}

function showLoading(msg) {
  elements.loadingOverlay.classList.remove("hidden");
  elements.loadingMsg.textContent = msg;
}

function hideLoading() {
  elements.loadingOverlay.classList.add("hidden");
}

function showInput() {
  elements.inputView.classList.remove("hidden");
  elements.resultsView.classList.add("hidden");
  document.getElementById("floating-dock-row").style.display = "flex";
}

function resetToInput() {
  stopRecordingIfActive();
  state.activeSessionId = null;
  hideError();
  elements.resultsView.classList.add("hidden");
  elements.loadingOverlay.classList.add("hidden");
  elements.inputView.classList.remove("hidden");
  document.getElementById("floating-dock-row").style.display = "flex";
  elements.transcriptInput.value = "";
  document.getElementById("dock-timer-display").textContent = "0:00";
  document.getElementById("live-badge").style.display = "none";
  state.lastInputWasAudio = false;
  highlightActiveHistoryItem();
}

function clearInput() {
  if (elements.transcriptInput) {
    elements.transcriptInput.value = "";
  }
  const audioInput = document.getElementById("audio-input");
  if (audioInput && typeof audioInput.value !== "undefined") {
    audioInput.value = "";
  }
  hideError();
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function initializeEventListeners() {
  const newSessionButton = document.getElementById("new-session-btn");
  if (newSessionButton) {
    newSessionButton.addEventListener("click", resetToInput);
  }

  if (elements.copyButton) {
    elements.copyButton.addEventListener("click", copyTranscriptToClipboard);
  }

  const clearInputButton = document.getElementById("clear-input-btn");
  if (clearInputButton) {
    clearInputButton.addEventListener("click", () => {
      if (typeof clearInput === "function") {
        clearInput();
      }
    });
  }

  const returnButton = document.getElementById("return-to-recorder-btn");
  if (returnButton) {
    returnButton.addEventListener("click", resetToInput);
  }

  const quickRecordButton = document.getElementById("nav-record-btn");
  if (quickRecordButton) {
    quickRecordButton.addEventListener("click", async () => {
      showInput();
      await toggleRecord();
    });
  }

  if (elements.settingsButton) {
    elements.settingsButton.addEventListener("click", openSettings);
  }

  if (elements.logoutButton) {
    elements.logoutButton.addEventListener("click", logout);
  }

  if (elements.exportButton) {
    elements.exportButton.addEventListener("click", () => {
      if (state.activeSessionId) {
        window.location.href = `/sessions/${state.activeSessionId}/export/text`;
      }
    });
  }

  const loginTab = document.getElementById("login-tab");
  if (loginTab) {
    loginTab.addEventListener("click", () => showAuthMode("login"));
  }

  const registerTab = document.getElementById("register-tab");
  if (registerTab) {
    registerTab.addEventListener("click", () => showAuthMode("register"));
  }

  if (elements.authSubmit) {
    elements.authSubmit.addEventListener("click", submitAuth);
  }

  const settingsSaveButton = document.getElementById("settings-save-btn");
  if (settingsSaveButton) {
    settingsSaveButton.addEventListener("click", saveApiKey);
  }

  const settingsCloseButton = document.getElementById("settings-close-btn");
  if (settingsCloseButton) {
    settingsCloseButton.addEventListener("click", closeSettings);
  }

  const settingsModal = document.getElementById("settings-modal");
  if (settingsModal) {
    settingsModal.addEventListener("click", (event) => {
      if (event.target === settingsModal) {
        closeSettings();
      }
    });
  }

  const submitTranscriptButton = document.getElementById("submit-transcript-btn");
  if (submitTranscriptButton) {
    submitTranscriptButton.addEventListener("click", submitTranscript);
  }

  const dockRecordButton = document.getElementById("dock-record-btn");
  if (dockRecordButton) {
    dockRecordButton.addEventListener("click", toggleRecord);
  }
}

async function initializeApp() {
  initializeEventListeners();
  renderLucideIcons();
  const user = await fetchCurrentUser();
  if (user) {
    await showAppView(user);
  } else {
    showAuthMode("login");
    showAuthView();
  }
}

initializeApp();
