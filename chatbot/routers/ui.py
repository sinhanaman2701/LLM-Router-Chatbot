from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["ui"])


def _build_page() -> str:
    return """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Anacity Chat</title>
    <style>
      :root {
        color-scheme: light;
        --bg: linear-gradient(180deg, #f5efe1 0%, #fffaf2 100%);
        --panel: rgba(255, 252, 245, 0.96);
        --border: #dccfb7;
        --text: #1f1b16;
        --muted: #6a6258;
        --accent: #a24d2a;
        --accent-strong: #7d3b20;
        --user: #efe2cf;
        --assistant: #fff;
        --error: #b42318;
      }

      * { box-sizing: border-box; }

      body {
        margin: 0;
        min-height: 100vh;
        font-family: "Avenir Next", "Segoe UI", sans-serif;
        background: var(--bg);
        color: var(--text);
      }

      .shell {
        width: min(920px, calc(100vw - 32px));
        margin: 24px auto;
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 24px;
        box-shadow: 0 20px 60px rgba(77, 48, 17, 0.12);
        overflow: hidden;
      }

      .hero {
        padding: 24px 24px 18px;
        border-bottom: 1px solid var(--border);
        background:
          radial-gradient(circle at top right, rgba(162, 77, 42, 0.14), transparent 38%),
          radial-gradient(circle at left center, rgba(179, 154, 118, 0.18), transparent 30%);
      }

      h1 {
        margin: 0 0 6px;
        font-size: clamp(1.8rem, 4vw, 2.6rem);
        line-height: 1;
        letter-spacing: -0.03em;
      }

      .subtitle {
        margin: 0;
        color: var(--muted);
      }

      .auth-bar {
        display: grid;
        grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr) auto;
        gap: 12px;
        padding: 18px 24px;
        border-bottom: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.55);
      }

      .auth-bar input,
      .composer textarea {
        width: 100%;
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 12px 14px;
        font: inherit;
        color: var(--text);
        background: #fffdf9;
      }

      .auth-bar button,
      .composer button {
        border: 0;
        border-radius: 14px;
        padding: 12px 18px;
        font: inherit;
        font-weight: 600;
        color: #fff;
        background: var(--accent);
        cursor: pointer;
      }

      .auth-bar button:hover,
      .composer button:hover { background: var(--accent-strong); }

      .auth-bar button:disabled,
      .composer button:disabled {
        cursor: not-allowed;
        opacity: 0.65;
      }

      .status {
        padding: 0 24px 14px;
        color: var(--muted);
        font-size: 0.95rem;
      }

      .status.error { color: var(--error); }

      .chat {
        height: 52vh;
        min-height: 360px;
        padding: 20px 24px;
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: 14px;
      }

      .empty {
        margin: auto 0;
        padding: 18px;
        border: 1px dashed var(--border);
        border-radius: 18px;
        text-align: center;
        color: var(--muted);
        background: rgba(255, 255, 255, 0.55);
      }

      .message {
        max-width: min(78%, 680px);
        padding: 14px 16px;
        border-radius: 18px;
        line-height: 1.45;
        white-space: pre-wrap;
        word-break: break-word;
        border: 1px solid rgba(0, 0, 0, 0.04);
      }

      .message.user {
        align-self: flex-end;
        background: var(--user);
      }

      .message.assistant {
        align-self: flex-start;
        background: var(--assistant);
      }

      .composer {
        padding: 18px 24px 24px;
        border-top: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.4);
      }

      .composer-inner {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 12px;
        align-items: end;
      }

      .composer textarea {
        min-height: 56px;
        max-height: 180px;
        resize: vertical;
      }

      @media (max-width: 780px) {
        .shell {
          width: min(100vw, calc(100vw - 20px));
          margin: 10px auto;
          border-radius: 18px;
        }

        .auth-bar,
        .composer-inner {
          grid-template-columns: 1fr;
        }

        .message { max-width: 92%; }
      }
    </style>
  </head>
  <body>
    <main class="shell">
      <section class="hero">
        <h1>Anacity Chat</h1>
        <p class="subtitle">Sign in, then chat with the facility-booking backend.</p>
      </section>

      <form class="auth-bar" id="login-form">
        <input id="email" name="email" type="email" placeholder="Email" required />
        <input id="password" name="password" type="password" placeholder="Password" required />
        <button id="login-button" type="submit">Login</button>
      </form>

      <div class="status" id="status">Log in to start chatting.</div>

      <section class="chat" id="messages" aria-live="polite">
        <div class="empty" id="empty-state">
          Your conversation will appear here after login.
        </div>
      </section>

      <form class="composer" id="chat-form">
        <div class="composer-inner">
          <textarea
            id="message-input"
            name="message"
            placeholder="Ask to book a facility..."
            disabled
            required
          ></textarea>
          <button id="send-button" type="submit" disabled>Send</button>
        </div>
      </form>
    </main>

    <script>
      const state = {
        token: null,
        sessionId: null,
        inFlight: false,
      };
      const POLL_INTERVAL_MS = 1200;
      const MAX_POLL_ATTEMPTS = 50;

      const loginForm = document.getElementById("login-form");
      const chatForm = document.getElementById("chat-form");
      const emailInput = document.getElementById("email");
      const passwordInput = document.getElementById("password");
      const loginButton = document.getElementById("login-button");
      const sendButton = document.getElementById("send-button");
      const messageInput = document.getElementById("message-input");
      const messages = document.getElementById("messages");
      const emptyState = document.getElementById("empty-state");
      const statusNode = document.getElementById("status");

      function setStatus(message, isError = false) {
        statusNode.textContent = message;
        statusNode.classList.toggle("error", isError);
      }

      function setChatEnabled(enabled) {
        messageInput.disabled = !enabled;
        sendButton.disabled = !enabled || state.inFlight;
      }

      function setLoginEnabled(enabled) {
        emailInput.disabled = !enabled;
        passwordInput.disabled = !enabled;
        loginButton.disabled = !enabled;
      }

      function addMessage(role, content) {
        if (emptyState) {
          emptyState.remove();
        }
        const node = document.createElement("article");
        node.className = "message " + role;
        node.textContent = content;
        messages.appendChild(node);
        messages.scrollTop = messages.scrollHeight;
      }

      async function pollStatus(requestId) {
        for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt += 1) {
          const response = await fetch("/chat/status/" + requestId, {
            method: "GET",
            headers: { "Authorization": "Bearer " + state.token },
          });

          if (!response.ok) {
            throw new Error("Polling failed with status " + response.status);
          }

          const payload = await response.json();
          if (payload.status === "processing") {
            await new Promise((resolve) => window.setTimeout(resolve, POLL_INTERVAL_MS));
            continue;
          }
          if (payload.status === "not_found") {
            throw new Error("The chat request was not found. Please try sending it again.");
          }
          return payload;
        }
        throw new Error("The assistant took too long to respond. Please try again.");
      }

      loginForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        setStatus("Signing in...");
        setLoginEnabled(false);

        try {
          const form = new FormData();
          form.append("email", emailInput.value);
          form.append("password", passwordInput.value);

          const response = await fetch("/auth/login", {
            method: "POST",
            body: form,
          });

          if (!response.ok) {
            throw new Error("Login failed");
          }

          const payload = await response.json();
          state.token = payload.token;
          state.sessionId = payload.session_id;

          setChatEnabled(true);
          setStatus(payload.recovery_message || "Logged in. You can start chatting now.");
          messageInput.focus();
        } catch (error) {
          state.token = null;
          state.sessionId = null;
          setChatEnabled(false);
          setStatus(error.message || "Login failed", true);
          setLoginEnabled(true);
          return;
        }

        setLoginEnabled(true);
      });

      chatForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!state.token || state.inFlight) {
          return;
        }

        const userMessage = messageInput.value.trim();
        if (!userMessage) {
          return;
        }

        state.inFlight = true;
        setChatEnabled(true);
        addMessage("user", userMessage);
        messageInput.value = "";
        setStatus("Waiting for assistant response...");

        try {
          const response = await fetch("/chat/message", {
            method: "POST",
            headers: {
              "Authorization": "Bearer " + state.token,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ user_message: userMessage }),
          });

          if (!response.ok) {
            throw new Error("Message request failed");
          }

          const accepted = await response.json();
          const result = await pollStatus(accepted.request_id);

          if (result.status === "done") {
            addMessage("assistant", result.response || "");
            setStatus("Response received.");
          } else if (result.status === "error") {
            setStatus(result.error || "The server returned an error.", true);
          } else {
            setStatus("Unexpected poll response.", true);
          }
        } catch (error) {
          setStatus(error.message || "Could not complete the request.", true);
        } finally {
          state.inFlight = false;
          setChatEnabled(true);
          messageInput.focus();
        }
      });
    </script>
  </body>
</html>
"""


@router.get("/", response_class=HTMLResponse)
async def chat_ui() -> HTMLResponse:
    return HTMLResponse(_build_page())
