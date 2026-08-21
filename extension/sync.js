// JobPilot — extension session sync.
//
// Runs on the JobPilot web app. When you're signed in there (any method, including
// Google), it reads the Supabase session that supabase-js keeps in localStorage and
// hands it to the extension's background script. That means you never sign into the
// extension separately: log in on the web app once and the extension is connected.
(function () {
  function readSession() {
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (!k || !/^sb-.*-auth-token$/.test(k)) continue;
        const raw = localStorage.getItem(k);
        if (!raw) continue;
        const v = JSON.parse(raw);
        const sess = (v && v.currentSession) ? v.currentSession : v;
        const at = sess && sess.access_token;
        const rt = sess && sess.refresh_token;
        if (at && rt) return { access_token: at, refresh_token: rt };
      }
    } catch (e) { /* ignore */ }
    return null;
  }

  function sync() {
    if (!(window.chrome && chrome.runtime && chrome.runtime.sendMessage)) return;
    const s = readSession();
    if (s) {
      try { chrome.runtime.sendMessage({ type: "syncSession", ...s }); } catch (e) {}
    }
  }

  sync();                                   // on page load
  window.addEventListener("focus", sync);   // and when the tab regains focus (token may have refreshed)
})();
