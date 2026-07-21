(function () {
  const body = document.body;
  const redirectUri = body.dataset.redirectUri || "";
  const callbackState = body.dataset.state || "";
  const errorEl = document.getElementById("auth-error");
  const formRoot = document.getElementById("auth-form");
  const successEl = document.getElementById("auth-success");
  const zPanel = document.getElementById("z-panel");
  const btnZ = document.getElementById("btn-z");

  function showError(msg) {
    if (!errorEl) return;
    errorEl.hidden = false;
    errorEl.textContent = msg || "Something went wrong.";
  }

  function clearError() {
    if (!errorEl) return;
    errorEl.hidden = true;
    errorEl.textContent = "";
  }

  async function postJson(path, payload) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      credentials: "include",
      body: JSON.stringify(payload),
    });
    let data = {};
    try {
      data = await res.json();
    } catch (_) {
      /* ignore */
    }
    return { ok: res.ok, status: res.status, data };
  }

  async function notifyCli(session) {
    if (!callbackState || !session) return;
    // Server bridge: CLI polls /v1/auth/cli/poll (works when localhost is blocked).
    try {
      await fetch("/v1/auth/cli/complete", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        credentials: "include",
        body: JSON.stringify({ state: callbackState, data: session }),
      });
    } catch (_) {
      /* ignore — still try localhost */
    }
    if (!redirectUri) return;
    try {
      await fetch(redirectUri, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: callbackState, data: session }),
      });
    } catch (_) {
      /* CLI may have closed or browser blocked loopback; poll bridge covers it */
    }
  }

  function showSuccessAndClose() {
    if (formRoot) formRoot.hidden = true;
    if (successEl) successEl.hidden = false;
    // Browsers only allow close for script-opened tabs; try anyway after a beat.
    setTimeout(function () {
      try {
        window.close();
      } catch (_) {
        /* ignore */
      }
    }, 1200);
  }

  async function finish(session) {
    if (!session || !session.access_token) {
      showError("Sign-in did not return a session.");
      return;
    }
    await notifyCli(session);
    showSuccessAndClose();
  }

  // Keep CLI redirect_uri/state when switching signup ↔ sign-in.
  (function preserveCliParamsOnSwitch() {
    const link = document.getElementById("auth-switch-link");
    if (!link) return;
    try {
      const url = new URL(link.getAttribute("href"), window.location.origin);
      const cur = new URL(window.location.href);
      ["redirect_uri", "state", "method"].forEach(function (key) {
        const v = cur.searchParams.get(key);
        if (v) url.searchParams.set(key, v);
      });
      link.setAttribute("href", url.pathname + url.search);
    } catch (_) {
      /* ignore */
    }
  })();

  // Server-rendered Google success path
  if (body.dataset.signedIn === "1") {
    let session = null;
    try {
      session = JSON.parse(body.dataset.session || "null");
    } catch (_) {
      session = null;
    }
    if (session) {
      notifyCli(session).then(showSuccessAndClose);
    } else {
      showSuccessAndClose();
    }
    return;
  }

  if (btnZ && zPanel) {
    btnZ.addEventListener("click", function () {
      zPanel.hidden = !zPanel.hidden;
    });
  }

  document.querySelectorAll(".auth-tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      const which = tab.dataset.tab;
      document.querySelectorAll(".auth-tab").forEach(function (t) {
        t.classList.toggle("active", t === tab);
      });
      const emailForm = document.getElementById("form-email");
      const phoneForm = document.getElementById("form-phone");
      if (emailForm) emailForm.hidden = which !== "email";
      if (phoneForm) phoneForm.hidden = which !== "phone";
    });
  });

  const emailForm = document.getElementById("form-email");
  if (emailForm) {
    emailForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      clearError();
      const email = (document.getElementById("email").value || "").trim();
      const name = (document.getElementById("name").value || "").trim();
      const btn = emailForm.querySelector('[data-action="send"]');
      if (btn) btn.disabled = true;
      const result = await postJson("/v1/auth/email/start", {
        email,
        name: name || null,
        method: "otp",
      });
      if (btn) btn.disabled = false;
      if (!result.ok) {
        showError(
          typeof result.data.detail === "string"
            ? result.data.detail
            : "Could not send email code."
        );
        return;
      }
      document.getElementById("email-code-block").hidden = false;
    });

    document.getElementById("email-verify").addEventListener("click", async function () {
      clearError();
      const email = (document.getElementById("email").value || "").trim();
      const name = (document.getElementById("name").value || "").trim();
      const code = (document.getElementById("email-code").value || "").trim();
      const result = await postJson("/v1/auth/email/verify", {
        email,
        code,
        name: name || null,
      });
      if (!result.ok || !result.data.access_token) {
        showError(
          typeof result.data.detail === "string"
            ? result.data.detail
            : "Invalid or expired code."
        );
        return;
      }
      await finish(result.data);
    });
  }

  const phoneForm = document.getElementById("form-phone");
  if (phoneForm) {
    phoneForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      clearError();
      const phone = (document.getElementById("phone").value || "").trim();
      const btn = phoneForm.querySelector('[data-action="send"]');
      if (btn) btn.disabled = true;
      const result = await postJson("/v1/auth/phone/start", { phone });
      if (btn) btn.disabled = false;
      if (!result.ok) {
        showError(
          typeof result.data.detail === "string"
            ? result.data.detail
            : "Could not send SMS code."
        );
        return;
      }
      document.getElementById("phone-code-block").hidden = false;
    });

    document.getElementById("phone-verify").addEventListener("click", async function () {
      clearError();
      const phone = (document.getElementById("phone").value || "").trim();
      const code = (document.getElementById("phone-code").value || "").trim();
      const result = await postJson("/v1/auth/phone/verify", { phone, code });
      if (!result.ok || !result.data.access_token) {
        showError(
          typeof result.data.detail === "string"
            ? result.data.detail
            : "Invalid or expired code."
        );
        return;
      }
      await finish(result.data);
    });
  }
})();
