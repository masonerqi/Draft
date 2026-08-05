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
  sidebarWidth: 280,
  sessions: [],
  currentView: "home",
  isResizingSidebar: false,
  sidebarResizeStartX: 0,
  sidebarResizeStartWidth: 280,
};

const elements = {
  appShell: document.getElementById("app-shell"),
  sidebarResizer: document.getElementById("sidebar-resizer"),
  profileName: document.getElementById("profile-name"),
  profileEmail: document.getElementById("profile-email"),
  profileInitials: document.getElementById("profile-initials"),
  historyList: document.getElementById("history-list"),
  homeView: document.getElementById("home-view"),
  searchView: document.getElementById("search-view"),
  searchResultsList: document.getElementById("search-results-list"),
  searchInput: document.getElementById("session-search-input"),
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

function showAppView(user) {
  state.currentUser = user;
  if (elements.appShell) elements.appShell.classList.remove("hidden");
  if (elements.profileName) elements.profileName.textContent = user.username;
  if (elements.profileEmail) elements.profileEmail.textContent = user.username;
  if (elements.profileInitials) elements.profileInitials.textContent = formatInitials(user.username);
  const settingsName = document.getElementById("settings-profile-name");
  const settingsEmail = document.getElementById("settings-profile-email");
  const settingsInitials = document.getElementById("settings-profile-initials");
  if (settingsName) settingsName.textContent = user.username;
  if (settingsEmail) settingsEmail.textContent = user.username;
  if (settingsInitials) settingsInitials.textContent = formatInitials(user.username);
  // Load user-specific data, but don't await here to allow quick UI response
  loadHistory();
  ensureApiKeySaved();
  showHome();
}

// Legacy inline auth removed. Authentication now happens on /login (Firebase-backed).
// The client must redirect to /login when no active session is found.

async function logout() {
  try {
    await fetch("/logout", { method: "POST", credentials: "same-origin" });
  } catch (error) {
    console.warn("Logout failed", error);
  }
  state.currentUser = null;
  // Redirect to login page
  window.location.href = "/login";
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
  elements.homeView.classList.add("hidden");
  elements.searchView.classList.add("hidden");
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
  elements.homeView.classList.add("hidden");
  elements.searchView.classList.add("hidden");
  elements.loadingOverlay.classList.add("hidden");
  elements.resultsView.classList.remove("hidden");
  document.getElementById("floating-dock-row").style.display = "none";
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
    state.sessions = Array.isArray(data) ? data : [];
    renderSessionList(elements.historyList, state.sessions.slice(0, 3), "No notes yet. Start your first recording when you are ready.");
    renderSessionList(elements.searchResultsList, state.sessions, "No matching notes found.");
  } catch (e) {
    console.warn("History unreachable.", e);
  }
}

function renderSessionList(container, sessions, emptyMessage) {
  if (!container) return;
  if (!sessions.length) {
    container.innerHTML = `<li class="history-empty">${emptyMessage}</li>`;
    return;
  }
  container.innerHTML = sessions
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
}

function filterSessions(query) {
  const normalized = query.trim().toLowerCase();
  const matches = normalized
    ? state.sessions.filter((session) => `${session.summary || ""} ${session.created_at || ""}`.toLowerCase().includes(normalized))
    : state.sessions;
  renderSessionList(elements.searchResultsList, matches, normalized ? "No matching notes found." : "No notes yet.");
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
  setActiveNav("settings-btn");
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
    setActiveNav("");
  } else if (state.currentView === "home") {
    showHome();
  } else if (state.currentView === "search") {
    showSearch();
  } else {
    showInput();
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
  state.currentView = "record";
  elements.homeView.classList.add("hidden");
  elements.searchView.classList.add("hidden");
  elements.inputView.classList.remove("hidden");
  elements.resultsView.classList.add("hidden");
  document.getElementById("floating-dock-row").style.display = "flex";
  setActiveNav("");
}

function showHome() {
  state.currentView = "home";
  elements.resultsView.classList.add("hidden");
  elements.searchView.classList.add("hidden");
  elements.inputView.classList.add("hidden");
  elements.homeView.classList.remove("hidden");
  document.getElementById("floating-dock-row").style.display = "none";
  setActiveNav("nav-home-btn");
  loadHistory();
}

async function showSearch() {
  state.currentView = "search";
  elements.resultsView.classList.add("hidden");
  elements.homeView.classList.add("hidden");
  elements.inputView.classList.add("hidden");
  elements.searchView.classList.remove("hidden");
  document.getElementById("floating-dock-row").style.display = "none";
  setActiveNav("nav-search-btn");
  await loadHistory();
  filterSessions(elements.searchInput.value || "");
  if (elements.searchInput) elements.searchInput.focus();
}

window.showSearch = showSearch;

function setActiveNav(activeId) {
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.id === activeId));
}

function resetToInput() {
  stopRecordingIfActive();
  state.activeSessionId = null;
  hideError();
  elements.resultsView.classList.add("hidden");
  elements.homeView.classList.add("hidden");
  elements.searchView.classList.add("hidden");
  elements.loadingOverlay.classList.add("hidden");
  elements.inputView.classList.remove("hidden");
  document.getElementById("floating-dock-row").style.display = "flex";
  elements.transcriptInput.value = "";
  document.getElementById("dock-timer-display").textContent = "0:00";
  document.getElementById("live-badge").style.display = "none";
  state.lastInputWasAudio = false;
  state.currentView = "record";
  setActiveNav("");
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

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function setSidebarWidth(width) {
  const nextWidth = clamp(width, 220, 420);
  state.sidebarWidth = nextWidth;
  document.documentElement.style.setProperty("--sidebar-width", `${nextWidth}px`);
  try {
    window.localStorage.setItem("sidebar-width", String(nextWidth));
  } catch (error) {
    console.warn("Unable to persist sidebar width", error);
  }
}

function initializeSidebarResize() {
  const resizer = elements.sidebarResizer;
  if (!resizer) {
    return;
  }

  const savedWidth = window.localStorage.getItem("sidebar-width");
  if (savedWidth) {
    const parsedWidth = Number.parseInt(savedWidth, 10);
    if (!Number.isNaN(parsedWidth)) {
      setSidebarWidth(parsedWidth);
    }
  } else {
    setSidebarWidth(state.sidebarWidth);
  }

  const stopResizing = () => {
    if (!state.isResizingSidebar) {
      return;
    }
    state.isResizingSidebar = false;
    resizer.classList.remove("resizing");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  };

  resizer.addEventListener("mousedown", (event) => {
    if (window.innerWidth <= 960) {
      return;
    }
    state.isResizingSidebar = true;
    state.sidebarResizeStartX = event.clientX;
    state.sidebarResizeStartWidth = state.sidebarWidth;
    resizer.classList.add("resizing");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    event.preventDefault();
  });

  window.addEventListener("mousemove", (event) => {
    if (!state.isResizingSidebar) {
      return;
    }
    const delta = event.clientX - state.sidebarResizeStartX;
    setSidebarWidth(state.sidebarResizeStartWidth + delta);
  });

  window.addEventListener("mouseup", stopResizing);
  window.addEventListener("mouseleave", stopResizing);
}

function initializeEventListeners() {
  const newSessionButton = document.getElementById("new-session-btn");
  if (newSessionButton) {
    newSessionButton.addEventListener("click", resetToInput);
  }

  const homeButton = document.getElementById("nav-home-btn");
  if (homeButton) homeButton.addEventListener("click", showHome);

  const homeRecordButton = document.getElementById("home-record-btn");
  if (homeRecordButton) {
    homeRecordButton.addEventListener("click", async () => {
      resetToInput();
      await toggleRecord();
    });
  }

  if (elements.searchInput) elements.searchInput.addEventListener("input", (event) => filterSessions(event.target.value));

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
  initializeSidebarResize();
  initializeEventListeners();
  renderLucideIcons();
  const user = await fetchCurrentUser();
  if (user) {
    showAppView(user);
  } else {
    // Redirect to the dedicated Firebase-based login page
    window.location.href = "/login";
  }
}

initializeApp();
