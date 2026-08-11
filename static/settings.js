import { initializeApp } from "https://www.gstatic.com/firebasejs/10.8.0/firebase-app.js";
import {
  getAuth,
  onAuthStateChanged,
  verifyBeforeUpdateEmail,
  sendEmailVerification,
} from "https://www.gstatic.com/firebasejs/10.8.0/firebase-auth.js";

// Same public Firebase config used on the sign-in page (templates/auth.html).
const firebaseConfig = {
  apiKey: "AIzaSyAf8ytTTyQo5ndX7PcetZFyp1W9ubs2aH4",
  authDomain: "draft-d36c3.firebaseapp.com",
  projectId: "draft-d36c3",
  storageBucket: "draft-d36c3.firebasestorage.app",
  messagingSenderId: "461424094330",
  appId: "1:461424094330:web:899ba79be001a2a843dfc3",
  measurementId: "G-K8YTKBYCC0",
};

const auth = getAuth(initializeApp(firebaseConfig));

function friendlyEmailUpdateError(code) {
  switch (code) {
    case "auth/requires-recent-login":
      return "For security, please log out and log back in before changing your email.";
    case "auth/email-already-in-use":
      return "This email address is already registered.";
    case "auth/invalid-email":
      return "Please enter a valid email address.";
    default:
      return "Something went wrong updating your email. Please try again.";
  }
}

// Firebase only applies a verifyBeforeUpdateEmail change once the user
// clicks the confirmation link (on a separate page), so the Flask session's
// cached email can go stale. Forcing a fresh ID token on every reload/re-auth
// and handing it to /api/sync-session keeps session['user_email'] current.
async function syncSessionWithServer(user) {
  try {
    // Forces Firebase to re-check the account server-side, so a
    // verifyBeforeUpdateEmail confirmation completed in another tab is
    // reflected here instead of a stale cached token.
    const idToken = await user.getIdToken(true);
    await fetch("/api/sync-session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ idToken }),
    });
  } catch (error) {
    console.warn("Unable to sync session with Firebase.", error);
  }
}

function initEmailUpdate() {
  const input = document.getElementById("email-address-input");
  const emailField = document.getElementById("email-address-field");
  const updateBtn = document.getElementById("update-email-btn");
  const resendBtn = document.getElementById("resend-verification-btn");
  const verifyBadge = document.getElementById("email-verify-badge");
  const errorEl = document.getElementById("email-update-error");
  const bannerEl = document.getElementById("email-update-banner");
  const googleNote = document.getElementById("google-account-note");
  const googleEmail = document.getElementById("google-account-email");
  if (!input || !updateBtn || !resendBtn || !errorEl || !bannerEl) return;

  function clearFeedback() {
    errorEl.textContent = "";
    bannerEl.classList.add("hidden");
    bannerEl.textContent = "";
  }

  // Google (and other federated) accounts don't have a Firebase password-auth
  // email to change here — Google owns that identity, so swap the editable
  // field for a plain read-only line instead of letting the user attempt it.
  // Password accounts that haven't confirmed their email get a small
  // "Unverified" badge plus an inline resend action instead of a full card.
  function applyProviderState(user) {
    const isGoogleUser = (user.providerData || []).some((p) => p.providerId === "google.com");
    emailField?.classList.toggle("hidden", isGoogleUser);
    updateBtn.style.display = isGoogleUser ? "none" : "";
    googleNote?.classList.toggle("hidden", !isGoogleUser);
    if (isGoogleUser && googleEmail) {
      googleEmail.textContent = user.email || "";
    }

    const needsVerification = !isGoogleUser && !user.emailVerified;
    verifyBadge?.classList.toggle("hidden", !needsVerification);
    resendBtn.classList.toggle("hidden", !needsVerification);
  }

  // Keep the field pre-filled with whichever address Firebase currently has
  // on file, unless the user is actively editing it.
  onAuthStateChanged(auth, (user) => {
    if (!user) return;
    if (user.email && document.activeElement !== input) {
      input.value = user.email;
    }
    applyProviderState(user);
    syncSessionWithServer(user);
  });

  updateBtn.addEventListener("click", async () => {
    clearFeedback();

    const newEmail = input.value.trim();
    if (!newEmail) {
      errorEl.textContent = "Please enter an email address.";
      return;
    }

    const user = auth.currentUser;
    if (!user) {
      errorEl.textContent = friendlyEmailUpdateError("auth/requires-recent-login");
      return;
    }

    updateBtn.disabled = true;
    try {
      await verifyBeforeUpdateEmail(user, newEmail);
      bannerEl.textContent = `A verification link has been sent to ${newEmail}. Please check your inbox to confirm the change.`;
      bannerEl.classList.remove("hidden");
    } catch (error) {
      console.error("Email update error:", error);
      errorEl.textContent = friendlyEmailUpdateError(error.code);
    } finally {
      updateBtn.disabled = false;
    }
  });

  resendBtn.addEventListener("click", async () => {
    clearFeedback();

    const user = auth.currentUser;
    if (!user) {
      errorEl.textContent = "You need to be signed in to resend the verification email.";
      return;
    }

    resendBtn.disabled = true;
    try {
      await sendEmailVerification(user);
      bannerEl.textContent = `A new verification link has been sent to ${user.email}.`;
      bannerEl.classList.remove("hidden");
    } catch (error) {
      console.error("Resend verification email error:", error);
      errorEl.textContent = error.code === "auth/too-many-requests"
        ? "Too many attempts. Please wait a bit before trying again."
        : "Unable to send the verification email right now. Please try again.";
    } finally {
      resendBtn.disabled = false;
    }
  });
}

function initDeleteAccount() {
  const deleteBtn = document.getElementById("delete-account-btn");
  const modal = document.getElementById("delete-account-modal");
  const closeBtn = document.getElementById("delete-account-close-btn");
  const confirmInput = document.getElementById("delete-account-confirm-input");
  const confirmBtn = document.getElementById("delete-account-confirm-btn");
  const errorEl = document.getElementById("delete-account-error");
  if (!deleteBtn || !modal || !confirmInput || !confirmBtn || !errorEl) return;

  function openModal() {
    confirmInput.value = "";
    confirmBtn.disabled = true;
    errorEl.textContent = "";
    modal.classList.remove("hidden");
  }

  function closeModal() {
    modal.classList.add("hidden");
  }

  deleteBtn.addEventListener("click", openModal);
  closeBtn.addEventListener("click", closeModal);
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeModal();
  });

  confirmInput.addEventListener("input", () => {
    confirmBtn.disabled = confirmInput.value.trim() !== "DELETE";
  });

  confirmBtn.addEventListener("click", async () => {
    errorEl.textContent = "";

    const user = auth.currentUser;
    if (!user) {
      errorEl.textContent = "You need to be signed in to delete your account.";
      return;
    }

    confirmBtn.disabled = true;

    // Captured before deletion so it can still identify the account to the
    // backend afterward — verify_id_token only checks the token's signature,
    // not whether the Firebase user still exists, so this stays valid for
    // the one cleanup call that follows.
    let idToken = null;
    try {
      idToken = await user.getIdToken();
    } catch (error) {
      console.warn("Unable to fetch ID token before deletion:", error);
    }

    // Firebase deletion goes FIRST and alone: if it fails (most commonly
    // auth/requires-recent-login), we halt right here without touching the
    // backend at all, so no local data is ever destroyed for an account
    // that's still alive in Firebase.
    try {
      await user.delete();
    } catch (error) {
      console.error("Firebase account deletion failed:", error);
      if (error.code === "auth/requires-recent-login") {
        alert("For security, deleting your account requires a recent login. Please log out and back in, then try again.");
      } else {
        errorEl.textContent = error.message || "Something went wrong deleting your account. Please try again.";
      }
      confirmBtn.disabled = confirmInput.value.trim() !== "DELETE";
      return;
    }

    // The Firebase account is gone at this point — best-effort purge of the
    // local database/session. A failure here can no longer be retried from
    // this screen (the user can't sign back in to try again), so it's
    // logged rather than blocking the redirect.
    try {
      const res = await fetch("/api/account/delete-data", {
        method: "POST",
        headers: idToken ? { Authorization: `Bearer ${idToken}` } : {},
        credentials: "same-origin",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        console.warn("Backend data purge failed after Firebase account deletion:", data.message || res.status);
      }
    } catch (error) {
      console.warn("Backend data purge request failed after Firebase account deletion:", error);
    }

    window.location.href = "/login?deleted=1";
  });
}

function initSettingsPage() {
  initEmailUpdate();
  initDeleteAccount();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initSettingsPage);
} else {
  initSettingsPage();
}
