/**
 * Z landing — terminal typing demo, install copy, waitlist form.
 */
(function () {
  "use strict";

  // ---- Terminal demo (self-contained, no backend) ----
  var LINES = [
    { html: '<span class="prompt">z&gt;</span> <span class="cmd">add stripe billing + tests</span>' },
    { html: '<span class="dim">checklist · product · verification · process</span>' },
    { html: '<span class="dim">… editing checkout, webhook, migration</span>' },
    { html: "" },
    { html: '<span class="ok">verify</span>  TESTS_PASSED  <span class="dim">discovered=7  exit=0</span>' },
    { html: '<span class="ok">process</span>  do-not-commit-until-verified  <span class="dim">satisfied</span>' },
    { html: "" },
    { html: '<span class="hi">Uncertainty tree</span> <span class="dim">(sort=risk · budget=3)</span>' },
    { html: '  1. Assumed Stripe response shape  <span class="tag">API Assumption</span>  <span class="hi">High</span>' },
    { html: '  2. Schema change — data impact?  <span class="tag">Migration Risk</span>  <span class="hi">Medium</span>' },
    { html: '  3. else branch on empty cart  <span class="tag">Edge Case</span>  <span class="hi">Medium</span>' },
    { html: "" },
    { html: '<span class="ok">3 blockers · /uncertainties · gate holds commit</span>' },
  ];

  function runTerminalDemo() {
    var body = document.getElementById("term-demo");
    if (!body) return;
    body.innerHTML = "";
    var cursor = document.createElement("span");
    cursor.className = "term-cursor";
    body.appendChild(cursor);

    var i = 0;
    function next() {
      if (i >= LINES.length) {
        // Restart after a pause
        setTimeout(function () {
          body.innerHTML = "";
          body.appendChild(cursor);
          i = 0;
          next();
        }, 4200);
        return;
      }
      var line = document.createElement("span");
      line.className = "line";
      line.innerHTML = LINES[i].html || "&nbsp;";
      body.insertBefore(line, cursor);
      // force reflow then reveal
      void line.offsetWidth;
      line.classList.add("visible");
      i += 1;
      var delay = LINES[i - 1].html === "" ? 280 : 420 + Math.min(220, (LINES[i - 1].html || "").length * 4);
      setTimeout(next, delay);
    }
    setTimeout(next, 500);
  }

  // ---- Copy install command(s) ----
  function setupCopy() {
    var buttons = document.querySelectorAll(".copy-install");
    if (!buttons.length) return;
    buttons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = btn.getAttribute("data-target");
        var code = id ? document.getElementById(id) : null;
        if (!code) return;
        var text = code.getAttribute("data-cmd") || code.textContent.replace(/^\$\s*/, "").trim();
        function done() {
          var prev = btn.textContent;
          btn.textContent = "Copied";
          btn.classList.add("copied");
          setTimeout(function () {
            btn.textContent = prev;
            btn.classList.remove("copied");
          }, 1600);
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done).catch(function () {
            fallbackCopy(text, done);
          });
        } else {
          fallbackCopy(text, done);
        }
      });
    });
  }

  function fallbackCopy(text, done) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
      done();
    } catch (e) {
      /* ignore */
    }
    document.body.removeChild(ta);
  }

  // ---- Waitlist form ----
  function setupWaitlist() {
    var form = document.getElementById("waitlist-form");
    if (!form) return;
    var errorEl = document.getElementById("waitlist-error");
    var successEl = document.getElementById("waitlist-success");
    var submitBtn = document.getElementById("waitlist-submit");

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      hideError();

      var first = (form.first_name.value || "").trim();
      var last = (form.last_name.value || "").trim();
      var email = (form.email.value || "").trim();

      if (!first || !last || !email) {
        showError("Please fill in all fields.");
        return;
      }
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        showError("Enter a valid email address.");
        return;
      }

      submitBtn.disabled = true;
      submitBtn.textContent = "Joining…";

      fetch("/v1/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          first_name: first,
          last_name: last,
          email: email,
        }),
      })
        .then(function (res) {
          return res.json().then(function (data) {
            return { ok: res.ok, status: res.status, data: data };
          }).catch(function () {
            return { ok: res.ok, status: res.status, data: {} };
          });
        })
        .then(function (result) {
          if (result.ok && result.data && result.data.ok) {
            form.style.display = "none";
            if (errorEl) errorEl.classList.remove("visible");
            if (successEl) successEl.classList.add("visible");
            return;
          }
          var msg =
            (result.data && (result.data.detail || result.data.message)) ||
            "Something went wrong — try again";
          if (typeof msg !== "string") msg = "Something went wrong — try again";
          showError(msg);
          submitBtn.disabled = false;
          submitBtn.textContent = "Join waitlist";
        })
        .catch(function () {
          showError("Something went wrong — try again");
          submitBtn.disabled = false;
          submitBtn.textContent = "Join waitlist";
        });
    });

    function showError(msg) {
      if (!errorEl) return;
      errorEl.textContent = msg;
      errorEl.classList.add("visible");
    }
    function hideError() {
      if (!errorEl) return;
      errorEl.classList.remove("visible");
      errorEl.textContent = "";
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    runTerminalDemo();
    setupCopy();
    setupWaitlist();
  });
})();
