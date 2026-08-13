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
  mediaStream: null,
  sidebarWidth: 280,
  sessions: [],
  currentView: "home",
  isResizingSidebar: false,
  sidebarResizeStartX: 0,
  sidebarResizeStartWidth: 280,
  folders: [],
  activeFolderId: null,
  selectMode: false,
  selectedSessionIds: new Set(),
  selectedFolderIcon: "folder",
  quotaPollTimer: null,
  lastQuota: null,
};

const elements = {
  appShell: document.getElementById("app-shell"),
  sidebarResizer: document.getElementById("sidebar-resizer"),
  profileName: document.getElementById("profile-name"),
  profileEmail: document.getElementById("profile-email"),
  profileInitials: document.getElementById("profile-initials"),
  historyList: document.getElementById("history-list"),
  homeView: document.getElementById("home-view"),
  homeTopbar: document.getElementById("home-topbar"),
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
  quotaAlert: document.getElementById("quota-alert"),
  quotaAlertText: document.getElementById("quota-alert-text"),
  quotaRingFill: document.getElementById("quota-ring-fill"),
  quotaRingValue: document.getElementById("quota-ring-value"),
  quotaPrimaryLabel: document.getElementById("quota-primary-label"),
  quotaPrimaryCaption: document.getElementById("quota-primary-caption"),
  quotaTierLabel: document.getElementById("quota-tier-label"),
  loadingOverlay: document.getElementById("loading"),
  loadingMsg: document.getElementById("loading-msg"),
  loadingContext: document.getElementById("loading-context"),
  loadingProgressFill: document.getElementById("loading-progress-fill"),
  loadingProgressPct: document.getElementById("loading-progress-pct"),
  errorBanner: document.getElementById("error-banner"),
  inputErrorBanner: document.getElementById("input-error-banner"),
  resultsSummary: document.getElementById("result-summary"),
  resultSummaryCard: document.getElementById("result-summary-card"),
  resultMeta: document.getElementById("result-meta"),
  resultTypeBadge: document.getElementById("result-type-badge"),
  resultOverview: document.getElementById("result-overview"),
  resultKeyConceptsCard: document.getElementById("result-key-concepts-card"),
  resultKeyConcepts: document.getElementById("result-key-concepts"),
  resultDecisionsCard: document.getElementById("result-decisions-card"),
  resultDecisionsTitle: document.getElementById("result-decisions-title"),
  resultDecisions: document.getElementById("result-decisions"),
  resultActionsCard: document.getElementById("result-actions-card"),
  resultActions: document.getElementById("result-actions"),
  transcriptSection: document.getElementById("transcript-section"),
  transcriptText: document.getElementById("transcript-text"),
  transcriptInput: document.getElementById("transcript-input"),
  languageSelect: document.getElementById("languageSelect"),
  importFileButton: document.getElementById("import-file-btn"),
  mediaFileInput: document.getElementById("media-file-input"),
  exportWrap: document.getElementById("export-wrap"),
  exportTrigger: document.getElementById("export-trigger"),
  exportMenu: document.getElementById("export-menu"),
  newSessionButton: document.getElementById("new-session-btn"),
  folderList: document.getElementById("folder-list"),
  createFolderButton: document.getElementById("create-folder-btn"),
  folderView: document.getElementById("folder-view"),
  folderViewTitle: document.getElementById("folder-view-title"),
  folderNotesList: document.getElementById("folder-notes-list"),
  deleteFolderButton: document.getElementById("delete-folder-btn"),
  createFolderModal: document.getElementById("create-folder-modal"),
  createFolderCloseButton: document.getElementById("create-folder-close-btn"),
  folderNameInput: document.getElementById("folder-name-input"),
  folderIconPicker: document.getElementById("folder-icon-picker"),
  createFolderError: document.getElementById("create-folder-error"),
  createFolderSubmitButton: document.getElementById("create-folder-submit-btn"),
  selectNotesButton: document.getElementById("select-notes-btn"),
  bulkActionsBar: document.getElementById("bulk-actions-bar"),
  bulkSelectedCount: document.getElementById("bulk-selected-count"),
  bulkFolderWrap: document.getElementById("bulk-folder-wrap"),
  bulkFolderTrigger: document.getElementById("bulk-folder-trigger"),
  bulkFolderMenu: document.getElementById("bulk-folder-menu"),
  bulkCancelButton: document.getElementById("bulk-cancel-btn"),
  bulkDeleteButton: document.getElementById("bulk-delete-btn"),
  summaryLanguageWrap: document.getElementById("summary-language-wrap"),
  summaryLanguageTrigger: document.getElementById("summary-language-trigger"),
  summaryLanguageMenu: document.getElementById("summary-language-menu"),
  summaryLanguageLabel: document.getElementById("summary-language-label"),
};

function renderLucideIcons() {
  if (window.lucide && typeof lucide.createIcons === "function") {
    lucide.createIcons();
  }
}

// Curated icon choices for the "Create folder" icon grid — all Lucide names,
// matching every other icon already used across the app.
const FOLDER_ICONS = [
  "folder", "book-open", "graduation-cap", "briefcase", "users", "star",
  "heart", "flag", "code", "coffee", "lightbulb", "target", "calendar",
  "music", "camera", "palette", "rocket", "globe",
];

// Settings > Preferences > Summary language. Mirrors gemini_client.py's
// LANGUAGE_NAMES exactly (same BCP-47 codes) — "" means "match transcript
// language" (Gemini's default when no language is specified).
const SUMMARY_LANGUAGE_OPTIONS = [
  { value: "", label: "Match transcript language" },
  { value: "en-US", label: "English (US)" },
  { value: "en-GB", label: "English (UK)" },
  { value: "ms-MY", label: "Bahasa Melayu" },
  { value: "zh-CN", label: "Mandarin (Simplified)" },
  { value: "ja-JP", label: "Japanese" },
  { value: "ko-KR", label: "Korean" },
  { value: "es-ES", label: "Spanish" },
  { value: "fr-FR", label: "French" },
  { value: "de-DE", label: "German" },
  { value: "hi-IN", label: "Hindi" },
  { value: "ta-IN", label: "Tamil" },
];

// Custom dropdown for #languageSelect: the native <select> stays the single
// source of truth (its data-flag attributes drive both the trigger button
// and the menu below), but is visually hidden — native <select> popups fall
// back to two-letter codes instead of pictorial flags on some platforms
// (e.g. Windows), while a browser-rendered custom menu doesn't.
function syncLanguageTrigger() {
  const select = elements.languageSelect;
  if (!select) return;
  const selected = select.options[select.selectedIndex];
  const flag = (selected && selected.dataset.flag) || "🌐";
  const label = selected ? selected.textContent.trim() : "";
  const flagEl = document.getElementById("language-select-flag");
  const labelEl = document.getElementById("language-select-label");
  if (flagEl) flagEl.textContent = flag;
  if (labelEl) labelEl.textContent = label;
  document.querySelectorAll(".language-select-option").forEach((el) => {
    const isActive = el.dataset.value === select.value;
    el.classList.toggle("active", isActive);
    el.setAttribute("aria-selected", String(isActive));
  });
}

function toggleLanguageMenu(forceOpen) {
  const menu = document.getElementById("language-select-menu");
  const trigger = document.getElementById("language-select-trigger");
  if (!menu || !trigger) return;
  const shouldOpen = typeof forceOpen === "boolean" ? forceOpen : menu.classList.contains("hidden");
  menu.classList.toggle("hidden", !shouldOpen);
  trigger.setAttribute("aria-expanded", String(shouldOpen));
}

function closeLanguageMenu() {
  toggleLanguageMenu(false);
}

function initializeLanguagePicker() {
  const select = elements.languageSelect;
  const trigger = document.getElementById("language-select-trigger");
  const menu = document.getElementById("language-select-menu");
  const wrap = document.getElementById("language-select-wrap");
  if (!select || !trigger || !menu || !wrap) return;

  Array.from(select.options).forEach((option) => {
    const item = document.createElement("li");
    item.className = "language-select-option";
    item.setAttribute("role", "option");
    item.dataset.value = option.value;
    item.innerHTML = `<span class="language-select-flag">${option.dataset.flag || "🌐"}</span><span>${option.textContent.trim()}</span>`;
    item.addEventListener("click", () => {
      select.value = option.value;
      syncLanguageTrigger();
      closeLanguageMenu();
    });
    menu.appendChild(item);
  });

  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleLanguageMenu();
  });
  document.addEventListener("click", (event) => {
    if (!wrap.contains(event.target)) closeLanguageMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeLanguageMenu();
  });

  syncLanguageTrigger();
}

// Mirrors the display-name fallback logic already used server-side in
// sidebar.html/settings.html: prefer the stored name, fall back to a
// title-cased email prefix, then "Guest".
function getFormattedDisplayName(user) {
  const name = user && user.name;
  const email = user && (user.email || user.username);
  if (name && name !== email) return name;
  if (email) {
    return email
      .split("@")[0]
      .replace(/\./g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }
  return "Guest";
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

async function showAppView(user) {
  state.currentUser = user;
  
  const displayName = getFormattedDisplayName(user);
  const email = user.email || user.username || "Not signed in";

  if (elements.appShell) elements.appShell.classList.remove("hidden");

  // Update Main Sidebar Profile
  if (elements.profileName) elements.profileName.textContent = displayName;
  if (elements.profileEmail) elements.profileEmail.textContent = email;
  if (elements.profileInitials) elements.profileInitials.textContent = formatInitials(displayName);

  // Update Settings Modal Profile
  const settingsName = document.getElementById("settings-profile-name");
  const settingsEmail = document.getElementById("settings-profile-email");
  const settingsInitials = document.getElementById("settings-profile-initials");

  if (settingsName) settingsName.textContent = displayName;
  if (settingsEmail) settingsEmail.textContent = email;
  if (settingsInitials) settingsInitials.textContent = formatInitials(displayName);

  // Fired in parallel rather than awaited one at a time — each hop is a
  // round trip to Postgres, and none of these three depends on another's
  // result, so serializing them only adds latency without buying anything.
  await Promise.all([loadHistory(), loadFolders(), ensureApiKeySaved()]);
  showHome();
}

// Re-fetches /me and re-paints just the name/initials shown in the sidebar
// and settings profile card — used after a Settings > Profile name save, as
// a lighter alternative to showAppView()'s full history/folders reload.
async function refreshProfileDisplay() {
  const user = await fetchCurrentUser();
  if (!user) return;
  state.currentUser = user;

  const displayName = getFormattedDisplayName(user);

  if (elements.profileName) elements.profileName.textContent = displayName;
  if (elements.profileInitials) elements.profileInitials.textContent = formatInitials(displayName);

  const settingsName = document.getElementById("settings-profile-name");
  const settingsInitials = document.getElementById("settings-profile-initials");
  if (settingsName) settingsName.textContent = displayName;
  if (settingsInitials) settingsInitials.textContent = formatInitials(displayName);
}
window.refreshProfileDisplay = refreshProfileDisplay;

// Legacy inline auth removed. Authentication now happens on /login (Firebase-backed).
// The client must redirect to /login when no active session is found.

async function logout() {
  try {
    await fetch("/logout", { method: "POST", credentials: "same-origin" });
  } catch (error) {
    console.warn("Logout failed", error);
  }
  state.currentUser = null;
  // Redirect to landing page
  window.location.href = "/";
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

// Fully clears the recording clock: stops the background interval, zeroes
// the counter, and resets the on-screen display. Used whenever a session is
// discarded/reset, as opposed to stopTimer() which just freezes the display
// at its current value when a recording ends naturally.
function resetTimer() {
  clearInterval(state.timerInterval);
  state.timerInterval = null;
  state.totalSeconds = 0;
  const timerDisplay = document.getElementById("dock-timer-display");
  if (timerDisplay) timerDisplay.textContent = "0:00";
}

// Stops any mic tracks captured for the permission/record session so the
// browser's recording indicator turns off and the device is freed for the
// next session.
function releaseMicrophone() {
  if (state.mediaStream) {
    state.mediaStream.getTracks().forEach((track) => track.stop());
    state.mediaStream = null;
  }
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
  // The language is only read when a new SpeechRecognition instance is
  // created, so changing it mid-session wouldn't take effect until the next
  // recording — disable it while recording to avoid that confusion.
  if (elements.languageSelect) elements.languageSelect.disabled = recording;
  const languageTrigger = document.getElementById("language-select-trigger");
  if (languageTrigger) {
    languageTrigger.disabled = recording;
    if (recording) closeLanguageMenu();
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
      state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      state.recognition = new SpeechRecognition();
      state.recognition.continuous = true;
      state.recognition.interimResults = true;
      // Bind the speech language from the recorder's language picker before
      // starting recognition. Web Speech API has no real "detect any
      // language automatically" mode — leaving .lang unset (the "Automatically
      // Detected" option's empty value) just falls back to the browser's own
      // default locale, it won't identify Mandarin vs. English mid-speech.
      const selectedLang = elements.languageSelect ? elements.languageSelect.value : "";
      if (selectedLang) {
        state.recognition.lang = selectedLang;
      }

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
        releaseMicrophone();
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
  // What language the summary is written in is Settings > Preferences'
  // "Summary language" choice (server-side, per-user) — not sent per
  // request. The picker here only ever controlled live speech recognition.
  const formData = new FormData();
  formData.append("transcript", text);
  formData.append("source_hint", state.lastInputWasAudio ? "voice" : "manual");
  await submitToAPI(formData, "text", estimateTextProcessingSeconds(text));
}

// Rough pacing hints for the loading animation, not a real prediction — a
// longer transcript genuinely tends to take Gemini a bit longer, so the
// progress bar ramps proportionally instead of using one fixed duration for
// every submission.
function estimateTextProcessingSeconds(text) {
  return Math.min(18, Math.max(5, text.length / 350));
}

function estimateMediaProcessingSeconds(file, durationSeconds) {
  const uploadEstimate = file.size / (1.5 * 1024 * 1024); // ~1.5MB/s, conservative
  const processEstimate = durationSeconds ? Math.max(10, durationSeconds * 0.15) : 22;
  return Math.min(150, Math.max(10, uploadEstimate + processEstimate));
}

// Matches app.py's MAX_CONTENT_LENGTH — checked client-side too so an
// oversized file fails fast instead of uploading first and failing later.
const MAX_UPLOAD_BYTES = 300 * 1024 * 1024;

// Mirrors quota.py's AUDIO_TOKENS_PER_SECOND / OUTPUT_TOKEN_BUFFER — a
// client-side version of the same estimate so an oversized file can warn
// before the user waits through an upload that the server will reject.
const CLIENT_AUDIO_TOKENS_PER_SECOND = 32;
const CLIENT_OUTPUT_TOKEN_BUFFER = 1500;

function getAudioDurationSeconds(file) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const probe = document.createElement("audio");
    const cleanup = () => URL.revokeObjectURL(url);
    probe.preload = "metadata";
    probe.onloadedmetadata = () => {
      const duration = Number.isFinite(probe.duration) ? probe.duration : null;
      cleanup();
      resolve(duration);
    };
    probe.onerror = () => {
      cleanup();
      resolve(null);
    };
    probe.src = url;
  });
}

async function warnIfAudioExceedsQuota(file, duration) {
  // Browser couldn't read the header (unsupported/corrupt file) — let the
  // server's own pre-flight check be the judge instead of blocking here.
  if (duration === null || duration === undefined) return true;

  const quotaData = state.lastQuota || (await fetchQuotaStatus());
  if (!quotaData) return true;

  const estimatedTokens = Math.round(duration * CLIENT_AUDIO_TOKENS_PER_SECOND) + CLIENT_OUTPUT_TOKEN_BUFFER;
  if (estimatedTokens <= quotaData.tpm.remaining) return true;

  return confirm(
    `This ~${Math.max(1, Math.round(duration / 60))} min file needs roughly ${formatQuotaCount(estimatedTokens)} tokens, ` +
    `but only about ${formatQuotaCount(quotaData.tpm.remaining)} are left this minute. It will likely be rate-limited. Upload anyway?`
  );
}

async function handleFileUpload(event) {
  const file = event.target.files && event.target.files[0];
  // Reset immediately (not just on success) so re-selecting the same
  // filename later — e.g. right after fixing an invalid API key — still
  // fires a fresh "change" event.
  event.target.value = "";
  if (!file) return;

  if (file.size > MAX_UPLOAD_BYTES) {
    alert(`That file is too large (max ${MAX_UPLOAD_BYTES / (1024 * 1024)}MB). Try a shorter recording or a more compressed format.`);
    return;
  }

  const duration = await getAudioDurationSeconds(file);
  if (!(await warnIfAudioExceedsQuota(file, duration))) return;

  stopRecordingIfActive();
  state.activeSessionId = null;
  state.lastInputWasAudio = true;

  const formData = new FormData();
  formData.append("media", file);
  await submitToAPI(formData, "media", estimateMediaProcessingSeconds(file, duration));
}

async function submitToAPI(formData, kind, estimatedSeconds) {
  showLoading(kind, estimatedSeconds);
  hideError();
  hideInputError();
  try {
    const res = await fetch("/summarise", {
      method: "POST",
      body: formData,
      credentials: "same-origin",
    });
    const data = await res.json();
    if (!res.ok) {
      const errorMessage = data.error || "Unknown server fault";
      // Covers "No Gemini API key configured...", "Gemini API key is
      // required...", and "...API key appears to be invalid or expired..."
      // — any backend error about the key sends the user straight to where
      // they'd fix it, instead of a generic alert.
      if (errorMessage.toLowerCase().includes("api key")) {
        openSettings("api-key");
        elements.settingsError.textContent = errorMessage;
        hideLoading();
        return;
      }
      // Rate-limit 429s (pre-flight rejection or a passthrough from a real
      // Gemini 429) carry a real reset time — surface that instead of a
      // generic message, and refresh the quota indicator in case Settings
      // is opened next.
      if (res.status === 429) {
        const resetSeconds = data.resets_in_seconds ?? data.retry_after_seconds;
        const resetText = typeof resetSeconds === "number" ? ` Try again in ${formatQuotaDuration(resetSeconds)}.` : "";
        hideLoading();
        showInput();
        showInputError(`${errorMessage}${resetText}`);
        fetchQuotaStatus();
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

// Minimal, intentionally-scoped markdown: only "**bold**" is supported,
// matching what the backend's system instructions ask Gemini to produce for
// bullet labels (see gemini_client.py's SYSTEM_INSTRUCTION_TEMPLATE). The
// source string is HTML-escaped first via the existing escapeHtml() helper,
// so the only tags this can ever introduce are the literal <strong> ones
// added below — raw markdown asterisks never reach the page, and summary
// content can't inject arbitrary HTML.
function renderBoldMarkdown(text) {
  return escapeHtml(text || "").replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

function renderBulletList(listEl, items) {
  listEl.innerHTML = "";
  items.forEach((item) => {
    const li = document.createElement("li");
    // li is a flex row (bullet dot + content) — without this wrapper span,
    // the <strong> label and the trailing text become two *separate* flex
    // items and each wraps independently in its own narrow column instead
    // of flowing together as one paragraph.
    li.innerHTML = `<span>${renderBoldMarkdown(item)}</span>`;
    listEl.appendChild(li);
  });
}

// detected_type is schema-constrained to exactly these four values (see
// gemini_client.py's DETECTED_TYPES) — mapped to a short label/icon for the
// badge next to "Summary Analysis".
const DETECTED_TYPE_BADGES = {
  "Academic / Lecture": { label: "Academic / Lecture", icon: "graduation-cap" },
  "Business Meeting": { label: "Business Meeting", icon: "briefcase" },
  "Technical Sync": { label: "Technical Sync", icon: "terminal" },
  "General Discussion": { label: "General Discussion", icon: "message-circle" },
};

// Backend timestamps (sessions.created_at, quota's cooldown_until) are UTC
// but not every row carries an explicit offset — older rows were written
// with a bare datetime.now().isoformat() before the backend started using
// datetime.now(timezone.utc). JS's Date constructor treats an offset-less
// date-time string as *local* time, not UTC, which silently shows the raw
// UTC clock numbers mislabeled as the viewer's local time. Appending "Z"
// whenever no offset is already present makes every timestamp resolve to
// the correct local time regardless of which format it was stored in.
function parseUtcTimestamp(raw) {
  if (!raw) return null;
  const isoLike = raw.includes(" ") && !raw.includes("T") ? raw.replace(" ", "T") : raw;
  const hasOffset = /Z$/.test(isoLike) || /[+-]\d{2}:?\d{2}$/.test(isoLike);
  return new Date(hasOffset ? isoLike : `${isoLike}Z`);
}

function renderResultMeta(data) {
  const meta = [];
  if (data.input_type) {
    const inputTypeLabels = { audio: "Voice Capture", media: "Imported File" };
    meta.push(inputTypeLabels[data.input_type] || "Pasted Text");
  }
  if (data.created_at) {
    meta.push(parseUtcTimestamp(data.created_at).toLocaleString());
  }
  elements.resultMeta.textContent = meta.join(" · ");
}

function renderTranscriptSection(data) {
  if (data.transcript && data.transcript.trim()) {
    elements.transcriptSection.classList.remove("hidden");
    elements.transcriptText.textContent = data.transcript;
    document.getElementById("transcript-toggle").classList.remove("open");
  } else {
    elements.transcriptSection.classList.add("hidden");
  }
}

// New adaptive schema: detected_type badge, an executive overview under the
// title, and three bulleted sections — each hidden entirely (not shown with
// filler text) when its array is empty.
function renderAdaptiveSummary(data) {
  elements.resultSummaryCard.classList.add("hidden");

  const badge = DETECTED_TYPE_BADGES[data.detected_type];
  if (badge) {
    elements.resultTypeBadge.innerHTML = `<i data-lucide="${badge.icon}"></i>${escapeHtml(badge.label)}`;
    elements.resultTypeBadge.classList.remove("hidden");
  } else {
    elements.resultTypeBadge.classList.add("hidden");
  }

  elements.resultOverview.textContent = data.overview || "";
  elements.resultOverview.classList.toggle("hidden", !data.overview);

  const keyConcepts = Array.isArray(data.key_concepts) ? data.key_concepts : [];
  elements.resultKeyConceptsCard.classList.toggle("hidden", keyConcepts.length === 0);
  renderBulletList(elements.resultKeyConcepts, keyConcepts);

  elements.resultDecisionsTitle.textContent = "Decisions & Takeaways";
  const decisions = Array.isArray(data.decisions_and_takeaways) ? data.decisions_and_takeaways : [];
  const actions = Array.isArray(data.action_items) ? data.action_items : [];
  elements.resultDecisionsCard.classList.toggle("hidden", decisions.length === 0);
  elements.resultActionsCard.classList.toggle("hidden", actions.length === 0);
  // If only one of this pair is visible, let it take the full width rather
  // than leaving an empty half-column gap next to it.
  elements.resultDecisionsCard.classList.toggle("result-card--wide", decisions.length > 0 && actions.length === 0);
  elements.resultActionsCard.classList.toggle("result-card--wide", actions.length > 0 && decisions.length === 0);
  renderBulletList(elements.resultDecisions, decisions);
  renderBulletList(elements.resultActions, actions);

  renderLucideIcons();
}

// Old flat shape (summary paragraph + decisions/action_items lists), for
// summary records saved before the adaptive schema shipped (no
// detected_type). Preserves the original filler-text behavior so these
// records keep looking exactly as they always have.
function renderLegacySummary(data) {
  elements.resultTypeBadge.classList.add("hidden");
  elements.resultOverview.classList.add("hidden");
  elements.resultKeyConceptsCard.classList.add("hidden");

  elements.resultSummaryCard.classList.remove("hidden");
  elements.resultsSummary.textContent = data.summary || "No summary available.";

  elements.resultDecisionsTitle.textContent = "Decisions Made";
  elements.resultDecisionsCard.classList.remove("hidden", "result-card--wide");
  elements.resultActionsCard.classList.remove("hidden", "result-card--wide");

  const decisions = Array.isArray(data.decisions) ? data.decisions : [];
  elements.resultDecisions.innerHTML = decisions.length === 0
    ? "<li>No concrete structural decisions resolved.</li>"
    : "";
  decisions.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    elements.resultDecisions.appendChild(li);
  });

  const actions = Array.isArray(data.action_items) ? data.action_items : [];
  elements.resultActions.innerHTML = actions.length === 0
    ? "<li>No pending contextual action items.</li>"
    : "";
  actions.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    elements.resultActions.appendChild(li);
  });
}

function showResults(data) {
  completeLoading();
  state.activeSessionId = data.session_id || data.id || null;

  renderResultMeta(data);
  if (data.detected_type) {
    renderAdaptiveSummary(data);
  } else {
    renderLegacySummary(data);
  }
  renderTranscriptSection(data);

  elements.inputView.classList.add("hidden");
  elements.homeView.classList.add("hidden");
  elements.homeTopbar.classList.add("hidden");
  elements.searchView.classList.add("hidden");
  elements.folderView.classList.add("hidden");
  elements.resultsView.classList.remove("hidden");
  document.getElementById("floating-dock-row").style.display = "none";
  clearFolderHighlight();
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
  elements.homeTopbar.classList.add("hidden");
  elements.searchView.classList.add("hidden");
  elements.loadingOverlay.classList.add("hidden");
  elements.resultsView.classList.remove("hidden");
  document.getElementById("floating-dock-row").style.display = "none";
}

function hideError() {
  elements.errorBanner.style.display = "none";
}

// A /summarise submission (rate limit, transient failure, ...) always starts
// from the input view, so its error belongs there — not on the results
// panel, which forcing open would surface whatever note was last viewed
// (stale content) underneath the banner instead of the form the user is
// actually looking at.
function showInputError(message) {
  hideError();
  elements.inputErrorBanner.textContent = message;
  elements.inputErrorBanner.style.display = "block";
}

function hideInputError() {
  elements.inputErrorBanner.style.display = "none";
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
  // Bulk multi-select is only ever active on the Search results list — Home's
  // mini-list and a folder's note list always render in normal (view/delete) mode.
  const selectable = state.selectMode && container === elements.searchResultsList;
  container.innerHTML = sessions
      .map((session) => {
        const titleBlock = `
            <div class="history-title">${escapeHtml(session.summary || "Untitled summary").substring(0, 55)}${(session.summary || "").length > 55 ? "..." : ""}</div>
            <div class="history-meta">${parseUtcTimestamp(session.created_at).toLocaleDateString()}</div>
          `;
        if (selectable) {
          const isChecked = state.selectedSessionIds.has(session.id);
          return `
        <li class="history-item selectable${isChecked ? " active" : ""}" data-id="${session.id}" onclick="toggleSessionSelected(${session.id})">
          <input type="checkbox" class="history-item-checkbox" ${isChecked ? "checked" : ""} tabindex="-1" />
          <div class="history-item-body">${titleBlock}</div>
        </li>
      `;
        }
        return `
        <li class="history-item" data-id="${session.id}">
          <div class="history-item-body" onclick="loadSession(${session.id})">${titleBlock}</div>
          <button type="button" aria-label="Delete session" onclick="event.stopPropagation(); deleteSession(${session.id})">
            <i data-lucide="trash-2"></i>
          </button>
        </li>
      `;
      })
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

// ---- Folders ----

async function loadFolders() {
  try {
    const res = await fetch("/folders", { credentials: "same-origin" });
    if (res.status === 401) {
      logout();
      return;
    }
    if (!res.ok) {
      console.warn("Folders request failed:", res.status);
      return;
    }
    const data = await res.json();
    state.folders = Array.isArray(data) ? data : [];
    renderFolderList();
    renderBulkFolderMenu();
  } catch (e) {
    console.warn("Folders unreachable.", e);
  }
}

function renderFolderList() {
  if (!elements.folderList) return;
  if (!state.folders.length) {
    elements.folderList.innerHTML = "";
    return;
  }
  elements.folderList.innerHTML = state.folders
    .map((folder) => `
      <li>
        <button type="button" class="nav-item folder-nav-item${state.activeFolderId === folder.id ? " active" : ""}" data-folder-id="${folder.id}" onclick="showFolder(${folder.id})">
          <i data-lucide="${escapeHtml(folder.icon)}"></i>
          <span>${escapeHtml(folder.name)}</span>
          ${folder.note_count ? `<span class="badge-red folder-count">${folder.note_count}</span>` : ""}
        </button>
      </li>
    `)
    .join("");
  renderLucideIcons();
}

window.showFolder = async function (id) {
  exitSelectMode();
  state.activeFolderId = id;
  const folder = state.folders.find((f) => f.id === id);

  state.currentView = "folder";
  elements.resultsView.classList.add("hidden");
  elements.homeView.classList.add("hidden");
  elements.homeTopbar.classList.add("hidden");
  elements.inputView.classList.add("hidden");
  elements.searchView.classList.add("hidden");
  elements.folderView.classList.remove("hidden");
  document.getElementById("floating-dock-row").style.display = "none";

  setActiveNav("");
  document.querySelectorAll(".folder-nav-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.folderId === String(id));
  });

  if (elements.folderViewTitle) elements.folderViewTitle.textContent = folder ? folder.name : "Folder";

  try {
    const res = await fetch(`/folders/${id}/sessions`, { credentials: "same-origin" });
    const data = await res.json();
    renderSessionList(elements.folderNotesList, Array.isArray(data) ? data : [], "No notes in this folder yet.");
  } catch (e) {
    console.warn("Unable to load folder notes.", e);
  }
};

async function deleteActiveFolder() {
  if (!state.activeFolderId) return;
  await fetch(`/folders/${state.activeFolderId}`, { method: "DELETE", credentials: "same-origin" });
  state.activeFolderId = null;
  await loadFolders();
  showHome();
}

function renderFolderIconPicker() {
  if (!elements.folderIconPicker) return;
  elements.folderIconPicker.innerHTML = FOLDER_ICONS
    .map((icon) => `
      <button type="button" class="icon-picker-option${state.selectedFolderIcon === icon ? " active" : ""}" data-icon="${icon}" title="${icon}">
        <i data-lucide="${icon}"></i>
      </button>
    `)
    .join("");
  renderLucideIcons();
  elements.folderIconPicker.querySelectorAll(".icon-picker-option").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.selectedFolderIcon = btn.dataset.icon;
      elements.folderIconPicker.querySelectorAll(".icon-picker-option").forEach((el) => {
        el.classList.toggle("active", el.dataset.icon === state.selectedFolderIcon);
      });
    });
  });
}

function openCreateFolderModal() {
  if (!elements.createFolderModal) return;
  state.selectedFolderIcon = "folder";
  if (elements.folderNameInput) elements.folderNameInput.value = "";
  if (elements.createFolderError) elements.createFolderError.textContent = "";
  renderFolderIconPicker();
  elements.createFolderModal.classList.remove("hidden");
  if (elements.folderNameInput) elements.folderNameInput.focus();
}

function closeCreateFolderModal() {
  if (elements.createFolderModal) elements.createFolderModal.classList.add("hidden");
}

async function submitCreateFolder() {
  const name = (elements.folderNameInput?.value || "").trim();
  if (elements.createFolderError) elements.createFolderError.textContent = "";
  if (!name) {
    if (elements.createFolderError) elements.createFolderError.textContent = "Please name your folder.";
    return;
  }
  try {
    const res = await fetch("/folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ name, icon: state.selectedFolderIcon }),
    });
    const data = await res.json();
    if (!res.ok) {
      if (elements.createFolderError) elements.createFolderError.textContent = data.error || "Failed to create folder.";
      return;
    }
    closeCreateFolderModal();
    await loadFolders();
  } catch (error) {
    if (elements.createFolderError) elements.createFolderError.textContent = error.message || "Unable to create folder.";
  }
}

// ---- Bulk select (Search view only) ----

function exitSelectMode() {
  if (!state.selectMode) return;
  state.selectMode = false;
  state.selectedSessionIds.clear();
  if (elements.bulkActionsBar) elements.bulkActionsBar.classList.add("hidden");
  if (elements.selectNotesButton) elements.selectNotesButton.querySelector("span").textContent = "Select";
  renderSessionList(elements.searchResultsList, state.sessions, "No matching notes found.");
}

function toggleSelectMode() {
  state.selectMode = !state.selectMode;
  state.selectedSessionIds.clear();
  if (elements.bulkActionsBar) elements.bulkActionsBar.classList.toggle("hidden", !state.selectMode);
  if (elements.selectNotesButton) elements.selectNotesButton.querySelector("span").textContent = state.selectMode ? "Cancel" : "Select";
  updateBulkSelectedCount();
  filterSessions(elements.searchInput.value || "");
}

window.toggleSessionSelected = function (id) {
  if (state.selectedSessionIds.has(id)) {
    state.selectedSessionIds.delete(id);
  } else {
    state.selectedSessionIds.add(id);
  }
  updateBulkSelectedCount();
  filterSessions(elements.searchInput.value || "");
};

function updateBulkSelectedCount() {
  if (elements.bulkSelectedCount) {
    elements.bulkSelectedCount.textContent = `${state.selectedSessionIds.size} selected`;
  }
}

function renderBulkFolderMenu() {
  if (!elements.bulkFolderMenu) return;
  if (!state.folders.length) {
    elements.bulkFolderMenu.innerHTML = `<li class="dropdown-option-empty">Create a folder first</li>`;
    return;
  }
  elements.bulkFolderMenu.innerHTML = state.folders
    .map((folder) => `
      <li class="dropdown-option" data-folder-id="${folder.id}">
        <i data-lucide="${escapeHtml(folder.icon)}"></i>
        <span>${escapeHtml(folder.name)}</span>
      </li>
    `)
    .join("");
  renderLucideIcons();
  elements.bulkFolderMenu.querySelectorAll(".dropdown-option").forEach((item) => {
    item.addEventListener("click", () => moveSelectedSessionsToFolder(Number(item.dataset.folderId)));
  });
}

async function moveSelectedSessionsToFolder(folderId) {
  if (!state.selectedSessionIds.size) return;
  try {
    await fetch("/sessions/folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ session_ids: Array.from(state.selectedSessionIds), folder_id: folderId }),
    });
  } catch (e) {
    console.warn("Unable to move notes to folder.", e);
  }
  toggleBulkFolderMenu(false);
  exitSelectMode();
  await Promise.all([loadFolders(), loadHistory()]);
  filterSessions(elements.searchInput.value || "");
}

async function deleteSelectedSessions() {
  const ids = Array.from(state.selectedSessionIds);
  if (!ids.length) return;
  if (!window.confirm(`Delete ${ids.length} note${ids.length > 1 ? "s" : ""}? This can't be undone.`)) {
    return;
  }
  try {
    await Promise.all(ids.map((id) => fetch(`/sessions/${id}`, { method: "DELETE", credentials: "same-origin" })));
  } catch (e) {
    console.warn("Unable to delete some notes.", e);
  }
  if (ids.includes(state.activeSessionId)) resetToInput();
  exitSelectMode();
  await Promise.all([loadHistory(), loadFolders()]);
}

function toggleBulkFolderMenu(forceOpen) {
  if (!elements.bulkFolderMenu || !elements.bulkFolderTrigger) return;
  const shouldOpen = typeof forceOpen === "boolean" ? forceOpen : elements.bulkFolderMenu.classList.contains("hidden");
  elements.bulkFolderMenu.classList.toggle("hidden", !shouldOpen);
}

function toggleExportMenu(forceOpen) {
  if (!elements.exportMenu || !elements.exportTrigger) return;
  const shouldOpen = typeof forceOpen === "boolean" ? forceOpen : elements.exportMenu.classList.contains("hidden");
  elements.exportMenu.classList.toggle("hidden", !shouldOpen);
}

// Called whenever another view (Home/Search/Input/Results) takes over, so a
// stale "currently open folder" highlight can't linger in the sidebar.
function clearFolderHighlight() {
  state.activeFolderId = null;
  document.querySelectorAll(".folder-nav-item").forEach((el) => el.classList.remove("active"));
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
  loadFolders();
  if (state.currentView === "folder" && state.activeFolderId) {
    showFolder(state.activeFolderId);
  }
};

function setSettingsTab(tab) {
  const target = tab || "profile";
  document.querySelectorAll(".settings-nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.settingsTab === target);
  });
  document.querySelectorAll(".settings-tab-panel").forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.settingsPanel !== target);
  });
  // The rate-limit indicator only lives on the API key tab, and only makes
  // sense to keep polling while it's actually visible.
  if (target === "api-key") {
    startQuotaPolling();
  } else {
    stopQuotaPolling();
  }
}

const QUOTA_POLL_INTERVAL_MS = 15000;
const QUOTA_WARNING_THRESHOLD = 0.8;

function formatQuotaCount(value) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value % 1_000_000 === 0 ? 0 : 1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(value % 1000 === 0 ? 0 : 1)}k`;
  return `${value}`;
}

function formatQuotaDuration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function quotaStatusColor(pct) {
  if (pct >= 1) return "var(--quota-critical)";
  if (pct >= QUOTA_WARNING_THRESHOLD) return "var(--quota-warn)";
  return "var(--quota-ok)";
}

const QUOTA_DIM_LABELS = { rpm: "Requests / min", tpm: "Tokens / min", rpd: "Requests / day" };
const QUOTA_RING_CIRCUMFERENCE = 188.5;

function renderQuotaUI(data) {
  if (!elements.quotaRingFill || !data) return;

  const dims = ["rpm", "tpm", "rpd"].map((key) => {
    const d = data[key];
    const pct = d.limit > 0 ? d.used / d.limit : 0;
    return { key, ...d, pct };
  });
  const primary = dims.reduce((worst, d) => (d.pct > worst.pct ? d : worst), dims[0]);
  const color = quotaStatusColor(primary.pct);

  dims.forEach(({ key, used, limit, pct }) => {
    const fillEl = document.getElementById(`quota-${key}-fill`);
    const countEl = document.getElementById(`quota-${key}-count`);
    const dimEl = document.querySelector(`.quota-dim[data-dim="${key}"]`);
    if (fillEl) {
      fillEl.style.width = `${Math.min(pct, 1) * 100}%`;
      fillEl.style.setProperty("--quota-color", quotaStatusColor(pct));
    }
    if (countEl) countEl.textContent = `${formatQuotaCount(used)} / ${formatQuotaCount(limit)}`;
    if (dimEl) dimEl.classList.toggle("quota-dim--critical", pct >= 1);
  });

  const ring = elements.quotaRingFill;
  const clampedPct = Math.min(primary.pct, 1);
  ring.style.setProperty("--quota-color", color);
  ring.style.setProperty("--quota-glow", clampedPct >= QUOTA_WARNING_THRESHOLD ? color : "transparent");
  ring.style.strokeDashoffset = `${QUOTA_RING_CIRCUMFERENCE * (1 - clampedPct)}`;
  if (elements.quotaRingValue) elements.quotaRingValue.textContent = Math.round(clampedPct * 100);

  if (elements.quotaPrimaryLabel) {
    const remaining = Math.max(primary.limit - primary.used, 0);
    const unit = primary.key === "tpm" ? "tokens" : "requests";
    const window = primary.key === "rpd" ? "today" : "this minute";
    elements.quotaPrimaryLabel.textContent = `${formatQuotaCount(remaining)} ${unit} left ${window}`;
  }
  if (elements.quotaPrimaryCaption) {
    elements.quotaPrimaryCaption.textContent = primary.pct >= 1
      ? `At capacity — resets in ${formatQuotaDuration(primary.resets_in_seconds)}`
      : `${QUOTA_DIM_LABELS[primary.key]} is your tightest limit right now`;
  }
  if (elements.quotaTierLabel) {
    elements.quotaTierLabel.textContent = `${data.tier === "tier_1" ? "Paid" : "Free"} tier`;
  }

  if (elements.quotaAlert) {
    const cooldownActive = data.cooldown_until && parseUtcTimestamp(data.cooldown_until) > new Date();
    if (cooldownActive) {
      elements.quotaAlert.classList.remove("hidden");
      elements.quotaAlertText.textContent = `Gemini asked this key to back off. Try again in ${formatQuotaDuration(primary.resets_in_seconds)}.`;
    } else if (primary.pct >= 1) {
      elements.quotaAlert.classList.remove("hidden");
      elements.quotaAlertText.textContent = `You're at capacity on ${QUOTA_DIM_LABELS[primary.key].toLowerCase()}. Resets in ${formatQuotaDuration(primary.resets_in_seconds)}.`;
    } else if (primary.pct >= QUOTA_WARNING_THRESHOLD) {
      elements.quotaAlert.classList.remove("hidden");
      elements.quotaAlertText.textContent = `Getting close to your ${QUOTA_DIM_LABELS[primary.key].toLowerCase()} limit — ${formatQuotaCount(Math.max(primary.limit - primary.used, 0))} left.`;
    } else {
      elements.quotaAlert.classList.add("hidden");
    }
  }

  renderLucideIcons();
}

async function fetchQuotaStatus() {
  try {
    const res = await fetch("/api/quota", { credentials: "same-origin" });
    if (!res.ok) return null;
    const data = await res.json();
    state.lastQuota = data;
    renderQuotaUI(data);
    return data;
  } catch (error) {
    console.warn("Unable to load quota status.", error);
    return null;
  }
}

function startQuotaPolling() {
  stopQuotaPolling();
  fetchQuotaStatus();
  state.quotaPollTimer = setInterval(fetchQuotaStatus, QUOTA_POLL_INTERVAL_MS);
}

function stopQuotaPolling() {
  if (state.quotaPollTimer) {
    clearInterval(state.quotaPollTimer);
    state.quotaPollTimer = null;
  }
}

function initializeSettingsTabs() {
  document.querySelectorAll(".settings-nav-item").forEach((btn) => {
    btn.addEventListener("click", () => setSettingsTab(btn.dataset.settingsTab));
  });
}

async function openSettings(tab) {
  setActiveNav("settings-btn");
  elements.settingsError.textContent = "";
  elements.settingsStatus.textContent = "";
  elements.apiKeyInput.value = "";
  setSettingsTab(tab);
  elements.settingsModal.classList.remove("hidden");
  await loadUserSettings();
  renderLucideIcons();
}

function closeSettings() {
  stopQuotaPolling();
  elements.settingsModal.classList.add("hidden");
  if (state.activeSessionId) {
    elements.resultsView.classList.remove("hidden");
    setActiveNav("");
  } else if (state.currentView === "home") {
    showHome();
  } else if (state.currentView === "search") {
    showSearch();
  } else if (state.currentView === "folder" && state.activeFolderId) {
    showFolder(state.activeFolderId);
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
    syncSummaryLanguageLabel(data.summary_language || "");
    return data;
  } catch (error) {
    elements.settingsError.textContent = error.message;
    return null;
  }
}

function syncSummaryLanguageLabel(value) {
  if (!elements.summaryLanguageLabel) return;
  const match = SUMMARY_LANGUAGE_OPTIONS.find((opt) => opt.value === value);
  elements.summaryLanguageLabel.textContent = match ? match.label : SUMMARY_LANGUAGE_OPTIONS[0].label;
  elements.summaryLanguageMenu?.querySelectorAll(".dropdown-option").forEach((el) => {
    el.classList.toggle("active", el.dataset.value === value);
  });
}

function toggleSummaryLanguageMenu(forceOpen) {
  if (!elements.summaryLanguageMenu) return;
  const shouldOpen = typeof forceOpen === "boolean" ? forceOpen : elements.summaryLanguageMenu.classList.contains("hidden");
  elements.summaryLanguageMenu.classList.toggle("hidden", !shouldOpen);
}

async function selectSummaryLanguage(value) {
  toggleSummaryLanguageMenu(false);
  syncSummaryLanguageLabel(value);
  try {
    await fetch("/api/user/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ summary_language: value }),
    });
  } catch (e) {
    console.warn("Unable to save summary language.", e);
  }
}

function initializeSummaryLanguagePicker() {
  if (!elements.summaryLanguageMenu || !elements.summaryLanguageTrigger) return;

  elements.summaryLanguageMenu.innerHTML = SUMMARY_LANGUAGE_OPTIONS
    .map((opt) => `<li class="dropdown-option" data-value="${opt.value}">${escapeHtml(opt.label)}</li>`)
    .join("");
  elements.summaryLanguageMenu.querySelectorAll(".dropdown-option").forEach((item) => {
    item.addEventListener("click", () => selectSummaryLanguage(item.dataset.value));
  });

  elements.summaryLanguageTrigger.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleSummaryLanguageMenu();
  });
  document.addEventListener("click", (event) => {
    if (elements.summaryLanguageWrap && !elements.summaryLanguageWrap.contains(event.target)) {
      toggleSummaryLanguageMenu(false);
    }
  });
}

async function ensureApiKeySaved() {
  const data = await loadUserSettings();
  if (data && !data.api_key_saved) {
    openSettings("api-key");
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

// Gemini gives no real progress events, so this is a paced, honest-feeling
// simulation, not a measurement: it eases toward ~92% over `estimatedSeconds`
// (derived from transcript length or file size/duration — see
// estimateTextProcessingSeconds/estimateMediaProcessingSeconds), then creeps
// slowly so a slower-than-expected request never looks stalled, and only
// ever reaches 100% via completeLoading() once the real response lands.
const LOADING_STAGES = {
  text: [
    { at: 0, msg: "Reading your transcript…" },
    { at: 25, msg: "Finding the key points…" },
    { at: 55, msg: "Drafting the summary…" },
    { at: 80, msg: "Polishing decisions & action items…" },
  ],
  media: [
    { at: 0, msg: "Uploading your recording…" },
    { at: 30, msg: "Gemini is transcribing the audio…" },
    { at: 62, msg: "Finding the key points…" },
    { at: 84, msg: "Drafting the summary…" },
  ],
};

let loadingTimer = null;

function setLoadingMessage(msg) {
  const el = elements.loadingMsg;
  if (el.textContent === msg) return;
  el.style.opacity = "0";
  setTimeout(() => {
    el.textContent = msg;
    el.style.opacity = "1";
  }, 180);
}

function setLoadingProgress(pct) {
  const clamped = Math.max(0, Math.min(100, pct));
  elements.loadingProgressFill.style.width = `${clamped}%`;
  elements.loadingProgressPct.textContent = `${Math.round(clamped)}%`;
}

function showLoading(kind, estimatedSeconds) {
  elements.loadingOverlay.classList.remove("hidden");
  stopLoadingTimer();

  const stages = LOADING_STAGES[kind] || LOADING_STAGES.text;
  const totalMs = Math.max(1, estimatedSeconds || 8) * 1000;
  const startedAt = performance.now();
  let currentStageIndex = -1;

  setLoadingProgress(0);
  setLoadingMessage(stages[0].msg);
  elements.loadingContext.textContent = kind === "media"
    ? "Longer recordings can take a minute or two."
    : "Usually just a few seconds.";

  loadingTimer = setInterval(() => {
    const elapsed = performance.now() - startedAt;
    let pct;
    if (elapsed < totalMs) {
      const t = elapsed / totalMs;
      pct = 92 * (1 - Math.pow(1 - t, 3)); // ease-out cubic, fast then slow
    } else {
      const overtimeMinutes = (elapsed - totalMs) / 60000;
      pct = Math.min(97, 92 + overtimeMinutes * 3);
      if (elapsed > totalMs + 25000) {
        elements.loadingContext.textContent = "Still working — this one's taking a little longer than usual.";
      }
    }
    setLoadingProgress(pct);

    const stageIdx = stages.reduce((acc, s, i) => (pct >= s.at ? i : acc), 0);
    if (stageIdx !== currentStageIndex) {
      currentStageIndex = stageIdx;
      setLoadingMessage(stages[stageIdx].msg);
    }
  }, 250);
}

function stopLoadingTimer() {
  if (loadingTimer) {
    clearInterval(loadingTimer);
    loadingTimer = null;
  }
}

// Used on failure/abort paths — disappears immediately, no fake completion.
function hideLoading() {
  stopLoadingTimer();
  elements.loadingOverlay.classList.add("hidden");
}

// Used only once the real response has actually arrived — fills the bar to
// 100% and holds it just long enough to read before the overlay clears, so
// success feels like a landing rather than an abrupt cut.
function completeLoading() {
  if (!loadingTimer && elements.loadingOverlay.classList.contains("hidden")) return;
  stopLoadingTimer();
  setLoadingProgress(100);
  setLoadingMessage("Done.");
  setTimeout(() => {
    elements.loadingOverlay.classList.add("hidden");
  }, 320);
}

function showInput() {
  state.currentView = "record";
  elements.homeView.classList.add("hidden");
  elements.homeTopbar.classList.add("hidden");
  elements.searchView.classList.add("hidden");
  elements.folderView.classList.add("hidden");
  elements.inputView.classList.remove("hidden");
  elements.resultsView.classList.add("hidden");
  document.getElementById("floating-dock-row").style.display = "flex";
  clearFolderHighlight();
  exitSelectMode();
  setActiveNav("");
}

function showHome() {
  state.currentView = "home";
  elements.resultsView.classList.add("hidden");
  elements.searchView.classList.add("hidden");
  elements.folderView.classList.add("hidden");
  elements.inputView.classList.add("hidden");
  elements.homeView.classList.remove("hidden");
  elements.homeTopbar.classList.remove("hidden");
  document.getElementById("floating-dock-row").style.display = "none";
  clearFolderHighlight();
  exitSelectMode();
  setActiveNav("nav-home-btn");
  loadHistory();
}

async function showSearch() {
  state.currentView = "search";
  elements.resultsView.classList.add("hidden");
  elements.homeView.classList.add("hidden");
  elements.homeTopbar.classList.add("hidden");
  elements.inputView.classList.add("hidden");
  elements.folderView.classList.add("hidden");
  elements.searchView.classList.remove("hidden");
  document.getElementById("floating-dock-row").style.display = "none";
  clearFolderHighlight();
  exitSelectMode();
  setActiveNav("nav-search-btn");
  await loadHistory();
  filterSessions(elements.searchInput.value || "");
  if (elements.searchInput) elements.searchInput.focus();
}

window.showSearch = showSearch;

function setActiveNav(activeId) {
  // Folder rows share the .nav-item class for consistent styling but have no
  // id (they're matched by data-folder-id instead) — exclude them here, or
  // setActiveNav("") would match every folder's empty id and highlight them all.
  document.querySelectorAll(".nav-item:not(.folder-nav-item)").forEach((item) => item.classList.toggle("active", item.id === activeId));
}

function resetToInput() {
  stopRecordingIfActive();
  resetTimer();
  releaseMicrophone();
  state.activeSessionId = null;
  state.liveTranscript = "";
  hideError();
  hideInputError();
  elements.resultsView.classList.add("hidden");
  elements.homeView.classList.add("hidden");
  elements.homeTopbar.classList.add("hidden");
  elements.searchView.classList.add("hidden");
  elements.folderView.classList.add("hidden");
  elements.loadingOverlay.classList.add("hidden");
  elements.inputView.classList.remove("hidden");
  document.getElementById("floating-dock-row").style.display = "flex";
  elements.transcriptInput.value = "";
  document.getElementById("live-badge").style.display = "none";
  state.lastInputWasAudio = false;
  state.currentView = "record";
  clearFolderHighlight();
  exitSelectMode();
  setActiveNav("");
  highlightActiveHistoryItem();
}

function clearInput() {
  stopRecordingIfActive();
  resetTimer();
  releaseMicrophone();
  state.liveTranscript = "";
  const liveBadge = document.getElementById("live-badge");
  if (liveBadge) liveBadge.style.display = "none";
  if (elements.transcriptInput) {
    elements.transcriptInput.value = "";
  }
  const audioInput = document.getElementById("audio-input");
  if (audioInput && typeof audioInput.value !== "undefined") {
    audioInput.value = "";
  }
  hideError();
  hideInputError();
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

const THEME_STORAGE_KEY = "draft-theme";

function applyTheme(theme) {
  const resolved = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", resolved);
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, resolved);
  } catch (error) {
    console.warn("Unable to persist theme preference", error);
  }
  const lightButton = document.getElementById("theme-light-btn");
  const darkButton = document.getElementById("theme-dark-btn");
  if (lightButton) {
    lightButton.classList.toggle("active", resolved === "light");
    lightButton.setAttribute("aria-checked", String(resolved === "light"));
  }
  if (darkButton) {
    darkButton.classList.toggle("active", resolved === "dark");
    darkButton.setAttribute("aria-checked", String(resolved === "dark"));
  }
}

function initializeTheme() {
  let saved;
  try {
    saved = window.localStorage.getItem(THEME_STORAGE_KEY);
  } catch (error) {
    saved = null;
  }
  // An inline script in index.html already set data-theme before first
  // paint to avoid a flash; this just syncs the toggle UI and covers pages
  // where that inline script hasn't run (defensive, same fallback logic).
  applyTheme(saved || document.documentElement.getAttribute("data-theme") || "dark");

  const lightButton = document.getElementById("theme-light-btn");
  const darkButton = document.getElementById("theme-dark-btn");
  if (lightButton) lightButton.addEventListener("click", () => applyTheme("light"));
  if (darkButton) darkButton.addEventListener("click", () => applyTheme("dark"));
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
    elements.settingsButton.addEventListener("click", () => openSettings());
  }

  if (elements.logoutButton) {
    elements.logoutButton.addEventListener("click", logout);
  }

  const settingsLogoutButton = document.getElementById("settings-logout-btn");
  if (settingsLogoutButton) {
    settingsLogoutButton.addEventListener("click", logout);
  }

  if (elements.exportTrigger) {
    elements.exportTrigger.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleExportMenu();
    });
  }
  if (elements.exportMenu) {
    elements.exportMenu.querySelectorAll(".dropdown-option").forEach((item) => {
      item.addEventListener("click", () => {
        const format = item.dataset.format;
        if (state.activeSessionId && format) {
          // The docx export renders the "Imported File" timestamp
          // server-side, so the browser's own resolved IANA zone is passed
          // along — the server otherwise has no way to know the viewer's
          // local timezone and would fall back to UTC.
          const tzParam = format === "docx" ? `?tz=${encodeURIComponent(Intl.DateTimeFormat().resolvedOptions().timeZone)}` : "";
          window.location.href = `/sessions/${state.activeSessionId}/export/${format}${tzParam}`;
        }
        toggleExportMenu(false);
      });
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

  if (elements.importFileButton && elements.mediaFileInput) {
    elements.importFileButton.addEventListener("click", () => elements.mediaFileInput.click());
    elements.mediaFileInput.addEventListener("change", handleFileUpload);
  }

  const dockRecordButton = document.getElementById("dock-record-btn");
  if (dockRecordButton) {
    dockRecordButton.addEventListener("click", toggleRecord);
  }

  if (elements.createFolderButton) {
    elements.createFolderButton.addEventListener("click", openCreateFolderModal);
  }
  if (elements.createFolderCloseButton) {
    elements.createFolderCloseButton.addEventListener("click", closeCreateFolderModal);
  }
  if (elements.createFolderSubmitButton) {
    elements.createFolderSubmitButton.addEventListener("click", submitCreateFolder);
  }
  if (elements.createFolderModal) {
    elements.createFolderModal.addEventListener("click", (event) => {
      if (event.target === elements.createFolderModal) closeCreateFolderModal();
    });
  }

  if (elements.deleteFolderButton) {
    elements.deleteFolderButton.addEventListener("click", () => {
      if (window.confirm("Delete this folder? Its notes will stay, unfiled.")) {
        deleteActiveFolder();
      }
    });
  }

  if (elements.selectNotesButton) {
    elements.selectNotesButton.addEventListener("click", toggleSelectMode);
  }
  if (elements.bulkCancelButton) {
    elements.bulkCancelButton.addEventListener("click", exitSelectMode);
  }
  if (elements.bulkDeleteButton) {
    elements.bulkDeleteButton.addEventListener("click", deleteSelectedSessions);
  }
  if (elements.bulkFolderTrigger) {
    elements.bulkFolderTrigger.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleBulkFolderMenu();
    });
  }
  document.addEventListener("click", (event) => {
    if (elements.bulkFolderWrap && !elements.bulkFolderWrap.contains(event.target)) {
      toggleBulkFolderMenu(false);
    }
    if (elements.exportWrap && !elements.exportWrap.contains(event.target)) {
      toggleExportMenu(false);
    }
  });
}

async function initializeApp() {
  initializeTheme();
  initializeSidebarResize();
  initializeLanguagePicker();
  initializeSettingsTabs();
  initializeSummaryLanguagePicker();
  initializeEventListeners();
  renderLucideIcons();
  const user = await fetchCurrentUser();
  if (user) {
    await showAppView(user);
  } else {
    // Redirect to the dedicated Firebase-based login page
    window.location.href = "/login";
  }
}


initializeApp();
