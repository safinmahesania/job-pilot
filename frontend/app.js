function jobpilot() {
  return {
    tab: 'feed', jobs: [], counts: {}, health: [], runs: [], errors: [], stats: null, loading: true, q: '', detail: null,
    running: false, lastRun: null, nextRun: null, threshold: 70,
    sort: 'score', source: 'all', sources: [],
    busy: null, snack: null, blocking: null, confirmBox: null,
    cover: null,   // { loading, text, provider, title, company }
    edit: null,    // { id, title, company, location, description, apply_url, ... } when editing a job by hand
    sourceTest: null,  // { name, loading, ok, count, jobs, error, elapsed_ms } when testing a source
    selectedSources: [],  // source names ticked for a selective run
    restarting: false,    // true while the server restarts and we wait for it to return
    scoring: false,       // true while scoring unscored jobs on demand
    llm: { providers: [], available: 0, total: 0, combined_tokens: 0, combined_limit: 0 },
    ai: { scoring: true, generation: true },
    cfgFiles: [],
    imp: { text: '', busy: false, result: null },
    // Multi-file email import. Files are processed one at a time so you can watch the
    // queue drain and see which file produced what — a single bulk call would hide
    // which email failed, and would tie up the server for one long request.
    impQueue: { items: [], running: false, done: 0, total: 0, totals: null },
    privacy: { mode: 'redacted', follow_job_links: true },
    fu: { items: [], total: 0, first: 0, second: 0, stale: 0 },
    digestPreview: '',
    guardError: null,
    fb: { saved: 0, applied: 0, dismissed: 0, high_scored_but_dismissed: 0,
          active: false, needed: 3, scoring_via_chain: true },
    privacyModes: [
      { key:'redacted', label:'Redacted (hosted models, no identifiers)',
        desc:"Your skills, projects and work history go to the model — it can't write about you otherwise. Your name, email, phone, address and profile links never appear in a prompt: the model writes around placeholders and JobPilot fills them in here, on this machine." },
      { key:'local', label:'Local only',
        desc:'Nothing personal leaves this machine. Ollama writes everything, with no cloud fallback — if Ollama is down the request fails rather than quietly going elsewhere. Strongest privacy, weaker writing.' },
      { key:'full', label:'Full (everything goes to the hosted model)',
        desc:'Contact details included. There is no quality gained by this over Redacted — it exists so the choice is visibly yours.' },
    ],
    clearDays: 30, mobileNav: false,
    modelState: { active: 'qwen2.5:14b', fallback_active: false, preferred: 'qwen2.5:14b' },
    selectedModel: 'qwen2.5:14b',
    notify: { enabled: true, configured: false },

    sched: { enabled: true, interval_hours: 8 },

    sourceCfg: [], newSource: { name: '', ats: 'greenhouse', identifier: '', query: '', active: true },
    atsTypes: ['greenhouse','lever','ashby','workday','oracle','phenom',
               'themuse','smartrecruiters','workable','jsearch','adzuna','remotive','remoteok',
               'weworkremotely','jobspresso','custom','aggregator','successfactors'],
    detectUrl: '', detecting: false,

    profile: null, profileRaw: '', rawMode: false, profileDirty: false,
    seniorityOpts: ['intern','junior','entry','mid','senior'],

    statuses: ['surfaced','saved','applied','interview','offer','rejected','dismissed'],
    jobsNav: [
      { k:'feed', label:'Feed', icon:'ti-inbox' },
      { k:'unscored', label:'Unscored', icon:'ti-help-circle' },
      { k:'saved', label:'Saved', icon:'ti-bookmark' },
      { k:'applied', label:'Applied', icon:'ti-send' },
      { k:'dismissed', label:'Dismissed', icon:'ti-archive' },
    ],
    sysNav: [
      { k:'importTab', label:'Import', icon:'ti-file-import' },
      { k:'stats', label:'Stats', icon:'ti-chart-bar' },
      { k:'sourcesTab', label:'Sources', icon:'ti-plug' },
      { k:'profile', label:'Profile', icon:'ti-user' },
      { k:'admin', label:'Admin', icon:'ti-activity-heartbeat' },
      { k:'settings', label:'Settings', icon:'ti-settings' },
    ],

    isJobView() { return ['feed','unscored','saved','applied','dismissed'].includes(this.tab); },
    tabLabel() {
      const all = [...this.jobsNav, ...this.sysNav].find(n => n.k === this.tab);
      return all ? all.label : this.tab;
    },

    async clearErrors() {
      if (!confirm('Clear the error log?')) return;
      await fetch('/api/errors/clear', { method: 'POST' }).catch(()=>{});
      this.errors = [];
    },

    async go(tab) {
      this.tab = tab;
      this.mobileNav = false;
      if (tab === 'sourcesTab') await this.loadSources();
      if (tab === 'profile') await this.loadProfile();
      if (tab === 'settings') { await this.loadLLM(); await this.loadAI(); await this.loadPrivacy(); await this.loadFeedback(); }
      await this.load();
    },

    async load() {
      this.loading = true;
      const jobsP = this.isJobView()
        ? fetch(`/api/jobs?tab=${this.tab}&sort=${this.sort}&source=${this.source}`).then(r=>r.json()).catch(()=>[])
        : Promise.resolve(this.jobs);
      const [jobs, counts, followups, health, runs, settings, stats, sources, sched, model, notifyState, errors] = await Promise.all([
        jobsP,
        fetch('/api/counts').then(r=>r.json()).catch(()=>({})),
        fetch('/api/followups').then(r=>r.json()).catch(()=>({items:[],total:0})),
        fetch('/api/health/assess').then(r=>r.json()).catch(()=>({boards:[]})),
        fetch('/api/runs').then(r=>r.json()).catch(()=>[]),
        fetch('/api/settings').then(r=>r.json()).catch(()=>({score_threshold:70})),
        fetch('/api/stats').then(r=>r.json()).catch(()=>null),
        fetch('/api/sources').then(r=>r.json()).catch(()=>[]),
        fetch('/api/schedule').then(r=>r.json()).catch(()=>null),
        fetch('/api/model').then(r=>r.json()).catch(()=>null),
        fetch('/api/notify').then(r=>r.json()).catch(()=>null),
        fetch('/api/errors').then(r=>r.json()).catch(()=>[]),
      ]);
      this.jobs = jobs;
      this.counts = { ...counts };
      this.fu = followups;
      this.health = health.boards || [];
      this.runs = runs || [];
      this.errors = errors || [];
      this.threshold = settings.score_threshold ?? 70;
      this.stats = stats;
      this.sources = sources;
      if (model) { this.modelState = model; this.selectedModel = model.preferred || model.active; }
      if (notifyState) this.notify = notifyState;
      if (sched) {
        this.sched = { enabled: sched.enabled, interval_hours: sched.interval_hours };
        this.running = sched.running;
        this.lastRun = sched.last_run;
        this.nextRun = sched.next_run;
        if (sched.running) this.poll();
      }
      this.loading = false;
    },

    // ───────── list <-> string helpers ─────────
    csv(a) { return Array.isArray(a) ? a.join(', ') : (a ?? ''); },
    toList(s) { return String(s).split(',').map(x => x.trim()).filter(Boolean); },
    lines(a) { return Array.isArray(a) ? a.join('\n') : (a ?? ''); },
    toLines(s) { return String(s).split('\n').map(x => x.trim()).filter(Boolean); },
    setList(obj, key, val) { obj[key] = this.toList(val); this.profileDirty = true; },
    setLines(obj, key, val) { obj[key] = this.toLines(val); this.profileDirty = true; },

    // ───────── profile ─────────
    async loadProfile() {
      const d = await fetch('/api/profile').then(r=>r.json()).catch(()=>({data:{}}));
      const p = d.data || {};
      p.identity   = p.identity   || {};
      p.constraints = p.constraints || {};
      p.search     = p.search     || {};
      p.skills     = p.skills     || {};
      p.experience = p.experience || [];
      p.projects   = p.projects   || [];
      p.education  = p.education  || [];
      for (const k of ['locations']) p.constraints[k] = p.constraints[k] || [];
      for (const k of ['role_levels','exclude_levels','domains','exclude_keywords','job_types'])
        p.search[k] = p.search[k] || [];
      for (const k of ['expert','proficient','familiar']) p.skills[k] = p.skills[k] || [];
      this.profile = p;
      this.profileDirty = false;
      if (this.rawMode) await this.loadRaw();
    },

    async loadRaw() {
      const d = await fetch('/api/profile/raw').then(r=>r.json()).catch(()=>({text:''}));
      this.profileRaw = d.text || '';
    },

    async toggleRaw() {
      this.rawMode = !this.rawMode;
      if (this.rawMode) await this.loadRaw();
      else await this.loadProfile();
    },

    async saveProfile() {
      const url  = this.rawMode ? '/api/profile/raw' : '/api/profile';
      const body = this.rawMode ? { text: this.profileRaw } : { data: this.profile };
      const r = await fetch(url, {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body),
      });
      const d = await r.json().catch(()=>({}));
      if (!r.ok) { this.showSnack(d.detail || 'Not saved', 'error'); return; }
      this.profileDirty = false;
      this.showSnack('Profile saved. Re-score to apply.');
    },

    addExp()  { this.profile.experience.push({ role:'', company:'', start:'', end:'', highlights:[] }); this.profileDirty = true; },
    addProj() { this.profile.projects.push({ name:'', tech:[], description:'', highlights:[] }); this.profileDirty = true; },
    addEdu()  { this.profile.education.push({ degree:'', field:'', institution:'', end:'', gpa:'' }); this.profileDirty = true; },
    async removeAt(list, i, what) {
      if (!(await this.ask(`Remove this ${what}?`))) return;
      list.splice(i, 1); this.profileDirty = true;
    },

    // ───────── model selector ─────────
    async saveModel() {
      await fetch('/api/model', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ model: this.selectedModel }),
      });
      this.showSnack('Scoring model: ' + this.selectedModel.replace('qwen2.5:',''));
      const m = await fetch('/api/model').then(r=>r.json()).catch(()=>null);
      if (m) this.modelState = m;
    },

    // ───────── telegram notify ─────────
    async saveNotify() {
      await fetch('/api/notify', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ enabled: this.notify.enabled }),
      });
      this.showSnack(this.notify.enabled ? 'Notifications on' : 'Notifications off');
    },
    async testNotify() {
      const r = await fetch('/api/notify/test', { method:'POST' }).then(r=>r.json()).catch(()=>({}));
      this.showSnack(r.sent ? 'Test sent — check Telegram' : 'Failed — check .env token', r.sent ? 'success' : 'error');
    },

    // ───────── scheduler ─────────
    async saveSchedule() {
      let h = parseFloat(this.sched.interval_hours);
      if (isNaN(h)) h = 8;
      this.sched.interval_hours = Math.max(0.5, Math.min(168, h));
      await fetch('/api/schedule', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ enabled: this.sched.enabled, interval_hours: this.sched.interval_hours }),
      });
      this.showSnack(this.sched.enabled ? `Auto-fetch every ${this.sched.interval_hours}h` : 'Auto-fetch off');
      await this.load();
    },

    // ───────── sources ─────────
    async loadSources() {
      this.sourceCfg = await fetch('/api/sources/config').then(r=>r.json()).catch(()=>[]);
    },
    async toggleSource(s) {
      await fetch(`/api/sources/${s.index}/toggle`, { method:'POST' });
      await this.loadSources();
      this.showSnack(`${s.name} ${s.active ? 'disabled' : 'enabled'}`);
    },
    async detectFromUrl() {
      const url = (this.detectUrl || '').trim();
      if (!url) { this.showSnack('Paste a careers-page URL first', 'error'); return; }
      this.detecting = true;
      try {
        const r = await fetch('/api/sources/detect', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        });
        const d = await r.json();
        // Pre-fill whatever the detector worked out; leave the name for the user.
        this.newSource.ats = d.ats || this.newSource.ats;
        this.newSource.identifier = d.identifier || d.tenant || '';
        if (d.careers_url) this.newSource.identifier = d.careers_url;
        if (d.needs_detail) {
          this.showSnack(d.note || 'Detected — but this ATS needs extra fields (edit companies.yaml)', 'error');
        } else {
          this.showSnack(`Detected: ${d.ats}${d.identifier ? ' · ' + d.identifier : ''}`);
        }
      } catch (e) {
        this.showSnack('Could not detect', 'error');
      }
      this.detecting = false;
    },
    async addSource() {
      if (!this.newSource.name.trim()) { this.showSnack('Name required', 'error'); return; }      const r = await fetch('/api/sources', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(this.newSource),
      });
      if (!r.ok) { this.showSnack('Failed to add', 'error'); return; }
      this.newSource = { name:'', ats:'greenhouse', identifier:'', query:'', active:true };
      await this.loadSources();
      this.showSnack('Source added');
    },
    async deleteSource(s) {
      if (!(await this.ask(`Remove "${s.name}" from sources?`))) return;
      await fetch(`/api/sources/${s.index}`, { method:'DELETE' });
      await this.loadSources();
      this.showSnack(`${s.name} removed`);
    },
    async testSource(s) {
      // Fetch just this one source and show what it pulls — no save, no scoring, no
      // full run. The fast way to check a source is wired up right.
      this.sourceTest = { name: s.name, loading: true, jobs: [] };
      try {
        const r = await fetch('/api/sources/test', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ index: s.index }),
        });
        const d = await r.json();
        this.sourceTest = { ...d, loading: false };
      } catch (e) {
        this.sourceTest = { name: s.name, loading: false, ok: false,
                            error: 'Request failed', jobs: [] };
      }
    },
    closeSourceTest() { this.sourceTest = null; },

    // ───────── jobs ─────────
    formatJD(text) {
      if (!text) return 'No description available.';

      // Job descriptions are scraped from job boards — untrusted HTML. The previous
      // version, if the text already contained a tag like <p> or <div>, returned it
      // RAW into x-html. A posting crafted with
      //   <img src=x onerror="fetch('/api/maint/nuclear',{method:'POST'})">
      // would then run in your logged-in browser, with your session. Stored XSS: the
      // payload lives in the database and fires on every view.
      //
      // So nothing scraped is ever trusted as markup. When the text already looks
      // like HTML, it is sanitised through an allowlist that keeps a handful of
      // formatting tags and DISCARDS every attribute — because the attribute is
      // where onerror, onload and href="javascript:" live. When it is plain text,
      // it is escaped and laid out line by line as before.
      const looksLikeHTML = /<(p|ul|ol|li|br|div|h[1-6]|strong|b|em|i)\b/i.test(text);
      return looksLikeHTML ? this.sanitizeHTML(text) : this.textToHTML(text);
    },

    // Plain text -> safe HTML. Every character is escaped; nothing here can be a tag.
    textToHTML(text) {
      const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      const ls = text.split('\n').map(l=>l.trim()).filter(l=>l.length);
      let html = '';
      for (const line of ls) {
        const isH = /^[A-Z][A-Z0-9 &/\-'’()]{3,}$/.test(line) || /^[A-Za-z ]{2,40}:$/.test(line);
        html += isH ? `<h4>${esc(line.replace(/:$/,''))}</h4>` : `<p>${esc(line)}</p>`;
      }
      return html;
    },

    // Untrusted HTML -> safe HTML, through an allowlist.
    //
    // The browser's own parser builds the tree (no regex trying to match HTML, which
    // never ends well), then this walks it and rebuilds a new tree containing ONLY
    // the tags on the list and NONE of their attributes. A <script>, an onerror, an
    // href="javascript:", an <iframe> — anything not on the list — simply does not
    // survive the copy. Text content is preserved; markup is not trusted.
    sanitizeHTML(dirty) {
      const ALLOWED = new Set(
        ['P','UL','OL','LI','BR','DIV','H1','H2','H3','H4','H5','H6',
         'STRONG','B','EM','I','SPAN']);

      const doc = new DOMParser().parseFromString(dirty, 'text/html');

      const clean = (node) => {
        const out = document.createDocumentFragment();
        for (const child of node.childNodes) {
          if (child.nodeType === Node.TEXT_NODE) {
            out.appendChild(document.createTextNode(child.textContent));
          } else if (child.nodeType === Node.ELEMENT_NODE
                     && ALLOWED.has(child.tagName)) {
            // A fresh element of the same tag, with no attributes carried over.
            const el = document.createElement(child.tagName.toLowerCase());
            el.appendChild(clean(child));      // recurse into the children
            out.appendChild(el);
          } else {
            // Disallowed element (script, img, iframe, a, ...): drop the tag but keep
            // whatever text was inside it, so content is never silently lost.
            out.appendChild(clean(child));
          }
        }
        return out;
      };

      const container = document.createElement('div');
      container.appendChild(clean(doc.body));
      return container.innerHTML;
    },

    filtered() {
      if (!this.q.trim()) return this.jobs;
      const s = this.q.toLowerCase();
      return this.jobs.filter(j =>
        (j.title||'').toLowerCase().includes(s) ||
        (j.company||'').toLowerCase().includes(s) ||
        (j.rationale||'').toLowerCase().includes(s));
    },

    openDetail(job) { this.detail = job; },

    // Generates either document — kind is 'cover' or 'resume'.
    async genDoc(job, kind = 'cover') {
      const meta = {
        cover:  { url: 'cover-letter', label: 'Cover letter', ext: 'txt', file: 'cover_letter' },
        resume: { url: 'resume',       label: 'Resume',       ext: 'md',  file: 'resume' },
      }[kind];

      this.cover = { loading: true, kind, text: '', provider: '',
                     label: meta.label, ext: meta.ext, file: meta.file,
                     jobId: job.id, title: job.title, company: job.company,
                     requirements: [], projects_used: [], overruns: [] };
      try {
        const r = await fetch(`/api/jobs/${job.id}/${meta.url}`, { method: 'POST' });
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          const d = err.detail;
          const noun = kind === 'cover' ? 'cover letter' : 'resume';

          // Two failures deserve more than a red toast, because both mean the
          // document you were about to send would have been false.
          if (d && d.error === 'profile_incomplete') {
            this.cover = null;
            this.guardError = {
              title: `Your profile is too empty to write a ${noun} from`,
              why: 'With these missing, the model has only the job posting to ' +
                   'write from — and it will write from it, inventing details ' +
                   'you never gave it.',
              items: d.missing,
              fix: 'Fill these into config/profile.yaml, then try again.',
            };
            return;
          }
          if (d && d.error === 'does_not_fit') {
            this.cover = null;
            this.guardError = {
              title: "You can't honestly apply to this one",
              why: 'Only ' + d.score + '% of what this job asks for appears anywhere ' +
                   'in your profile. A ' + noun + ' tailored to it would have to ' +
                   'invent the rest — and a model asked to tailor it will, fluently, ' +
                   'and you would be the one sending it. Nothing was generated.',
              items: d.matched.length
                ? ['All this job wants that you actually have: ' + d.matched.join(', ')]
                : ['Nothing this job asks for appears in your profile at all.'],
              fix: 'If you think this really is a good fit, your profile is missing ' +
                   'something. Add it, and try again.',
            };
            return;
          }
          if (d && d.error === 'fabricated') {
            this.cover = null;
            this.guardError = {
              title: `The model invented facts — the ${noun} was refused`,
              why: 'Nothing was returned to you. A ' + noun + ' claiming something ' +
                   'that is not in your profile is one careless send away from a ' +
                   'conversation you cannot recover from.',
              items: d.problems,
              fix: 'This usually means config/profile.yaml is thin, or a skill named ' +
                   'in the letter isn\'t listed there. The fuller your profile, the ' +
                   'less room there is for the model to invent — and the fewer false ' +
                   'refusals you\'ll see.',
            };
            return;
          }
          throw new Error((typeof d === 'string' ? d : d?.message) || `HTTP ${r.status}`);
        }
        const data = await r.json();
        this.cover.text = data.text;
        this.cover.provider = data.provider;
        this.cover.requirements = data.requirements || [];
        this.cover.projects_used = data.projects_used || [];
        this.cover.overruns = data.overruns || [];
        this.cover.warnings = data.warnings || [];
        this.cover.saved = false;
        this.loadLLM();                  // usage just changed
        this.saveMaterial();             // bind it to this job for the extension
      } catch (e) {
        this.cover = null;
        this.showSnack(`${meta.label} failed: ${e.message}`, 'error');
      } finally {
        if (this.cover) this.cover.loading = false;
      }
    },

    // Persist the document against its job. The browser extension attaches by
    // job id, so saving is what makes auto-attach possible — and what guarantees
    // the right company gets the right letter.
    async saveMaterial() {
      if (!this.cover?.text) return;
      try {
        await fetch(`/api/jobs/${this.cover.jobId}/materials`, {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({
            kind: this.cover.kind === 'resume' ? 'resume' : 'cover',
            content: this.cover.text,
            provider: this.cover.provider || '',
          }),
        });
        this.cover.saved = true;
        this.load();                     // refresh cards so the badge appears
      } catch {
        this.showSnack('Could not save to the job', 'error');
      }
    },

    async copyCover() {
      try {
        await navigator.clipboard.writeText(this.cover.text);
        this.showSnack('Copied to clipboard');
      } catch { this.showSnack('Copy failed', 'error'); }
    },

    async downloadCover(format = 'pdf') {
      // Save first. The textarea is editable, and the file you download must be
      // the version you are looking at — not the one the model first produced.
      await this.saveMaterial();
      const url = `/api/jobs/${this.cover.jobId}/materials/${this.cover.kind}/file?format=${format}`;
      const a = document.createElement('a');
      a.href = url;
      a.download = '';                 // the server sets the real filename
      a.click();
    },

    // ── AI providers ──
    async loadLLM() {
      try {
        this.llm = await (await fetch('/api/llm/providers')).json();
      } catch { /* panel just stays empty */ }
    },

    async toggleProvider(p) {
      p.enabled = !p.enabled;                       // optimistic
      await fetch(`/api/llm/providers/${p.name}/toggle`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ enabled: p.enabled }),
      });
      this.showSnack(`${p.label} ${p.enabled ? 'enabled' : 'disabled'}`);
      await this.loadLLM();
    },

    async moveProvider(i, delta) {
      const order = this.llm.providers.map(p => p.name);
      const j = i + delta;
      if (j < 0 || j >= order.length) return;
      [order[i], order[j]] = [order[j], order[i]];   // swap
      await fetch('/api/llm/providers/order', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ order }),
      });
      await this.loadLLM();
    },

    fmtNum(n) {
      if (n === null || n === undefined) return '—';
      return n.toLocaleString('en-US');
    },

    // ── AI features ──
    async loadAI() {
      try {
        this.ai = await (await fetch('/api/ai-features')).json();
        const cfg = await (await fetch('/api/config/files')).json();
        this.cfgFiles = cfg.files;
      } catch { /* leave defaults */ }
    },

    async toggleFeature(feature) {
      const enabled = !this.ai[feature];
      this.ai[feature] = enabled;                   // optimistic
      await fetch('/api/ai-features', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ feature, enabled }),
      });
      const label = feature === 'scoring' ? 'Scrape-time AI' : 'On-demand AI';
      this.showSnack(`${label} ${enabled ? 'on' : 'off'}`);
    },

    // ── Connection tests ──
    async testAI() {
      this.busy = { label: 'Testing AI…' };
      try {
        const r = await fetch('/api/llm/test', { method: 'POST' });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
        this.showSnack(`AI OK — answered by ${data.provider}`);
        await this.loadLLM();
      } catch (e) {
        this.showSnack('AI test failed: ' + e.message, 'error');
      } finally { this.busy = null; }
    },

    // ── Importing jobs ──
    async importFile(event) {
      const file = event.target.files?.[0];
      if (!file) return;
      this.imp.busy = true; this.imp.result = null;
      try {
        const form = new FormData();
        form.append('file', file);
        const r = await fetch('/api/import/file', { method: 'POST', body: form });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
        this.imp.result = data;
        this.showSnack(`Imported ${data.imported} of ${data.rows} rows`);
        await this.load();
      } catch (e) {
        this.showSnack('Import failed: ' + e.message, 'error');
      } finally {
        this.imp.busy = false;
        event.target.value = '';        // let the same file be picked again
      }
    },

    async importText() {
      if (!this.imp.text.trim()) return;
      this.imp.busy = true; this.imp.result = null;
      this.blocking = { label: 'Reading the posting…' };
      try {
        const r = await fetch('/api/import/text', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ text: this.imp.text }),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
        this.imp.result = data;
        this.imp.text = '';
        this.showSnack(`Added: ${data.job.title} · ${data.job.company}`);
        await this.load();
      } catch (e) {
        this.showSnack('Could not read that posting: ' + e.message, 'error');
      } finally {
        this.imp.busy = false; this.blocking = null;
      }
    },

    async importEmailFile(event) {
      const files = Array.from(event.target.files || []);
      event.target.value = '';                 // let the same file be picked again
      if (!files.length) return;

      this.imp.result = null;
      this.impQueue = {
        items: files.map(f => ({ name: f.name, status: 'pending', found: 0, error: '' })),
        running: true, done: 0, total: files.length,
        totals: { found: 0, imported: 0, scored: 0, unscored: 0,
                  dropped: 0, duplicates: 0, errors: 0 },
      };
      this.imp.busy = true;

      for (let i = 0; i < files.length; i++) {
        const item = this.impQueue.items[i];
        item.status = 'running';
        try {
          const form = new FormData();
          form.append('file', files[i]);
          const r = await fetch('/api/import/email-file', { method: 'POST', body: form });
          const data = await r.json();
          if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
          item.status = 'done';
          item.found = data.found || 0;
          item.imported = data.imported || 0;
          for (const k of Object.keys(this.impQueue.totals)) {
            this.impQueue.totals[k] += (data[k] || 0);
          }
        } catch (e) {
          item.status = 'failed';
          item.error = e.message;
          this.impQueue.totals.errors += 1;
        }
        this.impQueue.done = i + 1;
      }

      this.impQueue.running = false;
      this.imp.busy = false;
      const t = this.impQueue.totals;
      const failed = this.impQueue.items.filter(x => x.status === 'failed').length;
      this.showSnack(
        `${t.imported} imported from ${files.length - failed} of ${files.length} file${files.length === 1 ? '' : 's'}`,
        failed ? 'error' : 'success');
      await this.load();
    },

    clearImportQueue() {
      this.impQueue = { items: [], running: false, done: 0, total: 0, totals: null };
    },

    async importMailDrop() {
      this.imp.busy = true; this.imp.result = null;
      this.blocking = { label: 'Reading data/mail_drop/…' };
      try {
        const r = await fetch('/api/import/mail-drop', { method: 'POST' });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
        this.imp.result = data;
        this.showSnack(data.files
          ? `Read ${data.files} file(s) — imported ${data.imported}`
          : 'No emails in data/mail_drop/');
        await this.load();
      } catch (e) {
        this.showSnack('Mail drop failed: ' + e.message, 'error');
      } finally {
        this.imp.busy = false; this.blocking = null;
      }
    },

    // ── Privacy ──
    async loadPrivacy() {
      try {
        this.privacy = await (await fetch('/api/privacy')).json();
      } catch { /* keep the safe default */ }
    },

    async testDigest() {
      this.busy = 'digest';
      try {
        const r = await (await fetch('/api/notify/test-digest', { method: 'POST' })).json();
        this.digestPreview = r.preview;
        this.showSnack(r.sent ? 'Digest sent to Telegram'
                              : (r.reason || 'Not sent — preview below'),
                       r.sent ? 'ok' : 'error');
      } catch (e) {
        this.showSnack('Failed: ' + e.message, 'error');
      } finally { this.busy = null; }
    },

    // ── Follow-ups ──
    async loadFollowups() {
      try { this.fu = await (await fetch('/api/followups')).json(); } catch {}
    },

    async followup(id, action, days = 7) {
      try {
        const r = await fetch(`/api/jobs/${id}/followup`, {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ action, days }),
        });
        if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
        this.showSnack(action === 'done' ? 'Follow-up logged' : `Snoozed for ${days} days`);
        await this.load();              // refreshes the list, counts and follow-ups
      } catch (e) {
        this.showSnack('Could not update: ' + e.message, 'error');
      }
    },

    async loadFeedback() {
      try { this.fb = await (await fetch('/api/feedback')).json(); } catch {}
    },

    async saveScoring() {
      try {
        await fetch('/api/feedback/scoring', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ scoring_via_chain: this.fb.scoring_via_chain }),
        });
        this.showSnack(this.fb.scoring_via_chain
          ? 'Scoring now uses the provider chain'
          : 'Scoring pinned to local Ollama');
      } catch (e) { this.showSnack('Could not save: ' + e.message, 'error'); }
    },

    async savePrivacy() {
      try {
        const r = await fetch('/api/privacy', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({
            mode: this.privacy.mode,
            follow_job_links: this.privacy.follow_job_links,
          }),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
        this.privacy = { ...this.privacy, ...data };
        this.showSnack(
          this.privacy.mode === 'full'
            ? 'Contact details will now be sent to hosted models'
            : 'Privacy settings saved',
          this.privacy.mode === 'full' ? 'error' : 'success'
        );
      } catch (e) {
        this.showSnack('Could not save: ' + e.message, 'error');
      }
    },

    async copyPath(path) {
      try {
        await navigator.clipboard.writeText(path);
        this.showSnack('Path copied');
      } catch { this.showSnack('Copy failed', 'error'); }
    },

    async setStatus(job, status) {
      await fetch(`/api/jobs/${job.id}/status`, {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ status }),
      });
      this.load();
    },

    async saveNotes(job) {
      await fetch(`/api/jobs/${job.id}/notes`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ notes: job.notes || '' }),
      });
      this.showSnack('Note saved');
    },

    // ── Manual edit — fix a job the fetcher got wrong ──
    openEdit(job) {
      // Copy the editable fields into a working object so Cancel really cancels
      // (the card isn't mutated until Save succeeds).
      this.edit = {
        id: job.id,
        title: job.title || '',
        company: job.company || '',
        location: job.location || '',
        description: job.description || '',
        apply_url: job.apply_url || '',
        source_url: job.source_url || '',
        job_type: job.job_type || '',
        posted_date: job.posted_date || '',
        deadline: job.deadline || '',
        busy: false,
      };
    },

    cancelEdit() { this.edit = null; },

    async saveEdit() {
      if (!this.edit) return;
      this.edit.busy = true;
      const { id, busy, ...fields } = this.edit;   // don't send id/busy in the body
      try {
        const r = await fetch(`/api/jobs/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(fields),
        });
        if (!r.ok) {
          const msg = (await r.json().catch(() => ({}))).detail || 'Could not save';
          this.showSnack(typeof msg === 'string' ? msg : 'Could not save', 'error');
          this.edit.busy = false;
          return;
        }
        const data = await r.json().catch(() => ({}));
        this.edit = null;
        await this.load();          // pull the corrected job back into the list
        // If the edit changed something the score depends on, the job was re-scored
        // on the spot — say so, with the new number.
        if (data.rescored != null) {
          this.showSnack(`Job updated · re-scored to ${data.rescored}`);
        } else {
          this.showSnack('Job updated');
        }
      } catch (e) {
        this.showSnack('Could not save', 'error');
        this.edit.busy = false;
      }
    },

    async runNow(only = null) {
      const body = only && only.length ? { only } : {};
      const r = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (r.status === 409) { this.showSnack('Already running', 'error'); return; }
      this.running = true;
      this.blocking = {
        label: only && only.length
          ? `Fetching ${only.length} selected source${only.length > 1 ? 's' : ''}…`
          : 'Fetching and scoring jobs…',
      };
      this.poll();
    },

    // ── Selective run: pick sources, fetch just those (active state untouched) ──
    toggleSelected(name) {
      const i = this.selectedSources.indexOf(name);
      if (i === -1) this.selectedSources.push(name);
      else this.selectedSources.splice(i, 1);
    },
    isSelected(name) { return this.selectedSources.includes(name); },
    clearSelected() { this.selectedSources = []; },
    async runSelected() {
      if (!this.selectedSources.length) {
        this.showSnack('Tick at least one source first', 'error');
        return;
      }
      const picked = [...this.selectedSources];
      await this.runNow(picked);
      this.selectedSources = [];
    },

    async scoreAllUnscored() {
      const ids = this.jobs.map(j => j.id);
      if (!ids.length) return;
      this.scoring = true;
      try {
        const r = await fetch('/api/jobs/score', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ job_ids: ids }),
        });
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          this.showSnack(err.detail || 'Scoring failed — is a model available?', 'error');
          return;
        }
        const data = await r.json();
        this.showSnack(`Scored ${data.scored} of ${data.requested}`, data.scored ? 'success' : 'info');
        await this.load();          // refresh — scored jobs leave the unscored tab, counts update too
      } catch (e) {
        this.showSnack('Scoring failed — check the server', 'error');
      } finally {
        this.scoring = false;
      }
    },

    async restartServer() {
      if (!confirm('Restart the server? It will reconnect in a few seconds.')) return;
      this.restarting = true;
      try {
        await fetch('/api/maint/restart', { method: 'POST' });
      } catch (e) { /* the process may drop the connection mid-reply — that's expected */ }

      // Poll until the server answers again, then reload the page onto the fresh process.
      const deadline = Date.now() + 30000;
      const ping = async () => {
        try {
          const r = await fetch('/api/run/status', { cache: 'no-store' });
          if (r.ok) { this.showSnack('Server is back', 'success'); location.reload(); return; }
        } catch (e) { /* still down */ }
        if (Date.now() < deadline) {
          setTimeout(ping, 1500);
        } else {
          this.restarting = false;
          this.showSnack('Server did not come back — check the terminal', 'error');
        }
      };
      setTimeout(ping, 2500);   // give it a moment to actually go down first
    },

    poll() {
      if (this._pollIv) return;
      this._pollIv = setInterval(async () => {
        const s = await fetch('/api/run/status').then(r=>r.json()).catch(()=>null);
        const m = await fetch('/api/model').then(r=>r.json()).catch(()=>null);
        if (m) this.modelState = m;
        if (!s) return;
        this.running = s.running; this.lastRun = s.last_run; this.nextRun = s.next_run;
        if (!s.running) {
          clearInterval(this._pollIv); this._pollIv = null;
          this.blocking = null;
          fetch('/api/model').then(r=>r.json()).then(m => { if (m) this.modelState = m; }).catch(()=>{});
          this.showSnack('Run complete');
          this.load();
        }
      }, 3000);
    },

    // ───────── maintenance ─────────
    async maint(action, opts = {}) {
      if (opts.confirm && !(await this.ask(opts.confirm))) return;
      if (opts.heavy) this.blocking = { label: opts.busyLabel || (opts.label + '…') };
      else this.busy = { label: opts.busyLabel || (opts.label + '…') };
      try {
        const r = await fetch(opts.url, {
          method: opts.method || 'POST',
          headers: {'Content-Type':'application/json'},
          body: opts.body ? JSON.stringify(opts.body) : undefined,
        });
        this.showSnack(opts.label + ' — ' + this.summarize(await r.json()));
      } catch (e) {
        this.showSnack('Failed: ' + e, 'error');
      } finally { this.busy = null; this.blocking = null; }
      await this.load();
    },
    exportCsv() { window.location.href = '/api/maint/export'; },
    summarize(d) { return (d && typeof d === 'object')
      ? Object.entries(d).map(([k,v]) => `${v} ${k}`).join(', ') : String(d); },

    // ───────── settings ─────────
    async saveThreshold() {
      this.clampThreshold();
      await fetch('/api/settings/threshold', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ value: this.threshold }),
      });
      await this.load();
    },
    clampThreshold() {
      if (this.threshold === '' || isNaN(this.threshold)) this.threshold = 70;
      this.threshold = Math.max(0, Math.min(100, Math.round(this.threshold)));
    },

    // ───────── ui helpers ─────────
    ask(msg) { return new Promise(res => { this.confirmBox = { msg, resolve: res }; }); },
    confirmYes() { const c = this.confirmBox; this.confirmBox = null; c && c.resolve(true); },
    confirmNo()  { const c = this.confirmBox; this.confirmBox = null; c && c.resolve(false); },
    showSnack(msg, type = 'success') {
      this.snack = { msg, type };
      clearTimeout(this._snackT);
      this._snackT = setTimeout(() => { this.snack = null; }, 3500);
    },
    // Only shown when the employer actually published a figure — most don't.
    salaryLabel(job) {
      const k = (n) => (n >= 1000 ? `${Math.round(n / 1000)}k` : String(n));
      if (job.salary_min && job.salary_max && job.salary_min !== job.salary_max) {
        return `$${k(job.salary_min)}–${k(job.salary_max)}`;
      }
      return `$${k(job.salary_max || job.salary_min)}`;
    },

    // A board that returns nothing but reports success is not "ok".
    brokenBoards() {
      return this.health.filter(b =>
        ['silent', 'erroring', 'never_worked'].includes(b.verdict));
    },

    tier(score) {
      // Unscored (imported without a description): neutral, never a fake colour.
      if (score === null || score === undefined) {
        return { bg:'#F3F4F6', fg:'#9CA3AF', stripe:'#D1D5DB' };
      }
      if (score >= 80) return { bg:'#1D9E75', fg:'#fff', stripe:'#1D9E75' };
      if (score >= 70) return { bg:'#EDDCB8', fg:'#7A4E0C', stripe:'#B4791A' };
      return { bg:'#E5E7EB', fg:'#374151', stripe:'#E5E7EB' };
    },
    cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); },
    modelShort(m) { return (m || '').replace('qwen2.5:',''); },
    timeAgo(d) { try { const days = Math.floor((Date.now() - new Date(d)) / 8.64e7); return days <= 0 ? 'today' : days + 'd ago'; } catch { return ''; } },
  };
}
