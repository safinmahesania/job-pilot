function jobpilot() {
  return {
    tab: 'feed', jobs: [], counts: {}, health: [], runs: [], stats: null, loading: true, q: '', detail: null,
    running: false, lastRun: null, nextRun: null, threshold: 70,
    sort: 'score', source: 'all', sources: [],
    busy: null, snack: null, blocking: null, confirmBox: null,
    cover: null,   // { loading, text, provider, title, company }
    clearDays: 30, mobileNav: false,
    modelState: { active: 'qwen2.5:14b', fallback_active: false, preferred: 'qwen2.5:14b' },
    selectedModel: 'qwen2.5:14b',
    notify: { enabled: true, configured: false },

    sched: { enabled: true, interval_hours: 8 },

    sourceCfg: [], newSource: { name: '', ats: 'greenhouse', identifier: '', query: '', active: true },
    atsTypes: ['greenhouse','lever','ashby','workday','oracle','phenom',
               'themuse','remotive','remoteok','weworkremotely','jobspresso'],

    profile: null, profileRaw: '', rawMode: false, profileDirty: false,
    seniorityOpts: ['intern','junior','entry','mid','senior'],

    statuses: ['surfaced','saved','applied','interview','offer','rejected','dismissed'],
    jobsNav: [
      { k:'feed', label:'Feed', icon:'ti-inbox' },
      { k:'saved', label:'Saved', icon:'ti-bookmark' },
      { k:'applied', label:'Applied', icon:'ti-send' },
      { k:'dismissed', label:'Dismissed', icon:'ti-archive' },
    ],
    sysNav: [
      { k:'stats', label:'Stats', icon:'ti-chart-bar' },
      { k:'sourcesTab', label:'Sources', icon:'ti-plug' },
      { k:'profile', label:'Profile', icon:'ti-user' },
      { k:'admin', label:'Admin', icon:'ti-activity-heartbeat' },
      { k:'settings', label:'Settings', icon:'ti-settings' },
    ],

    isJobView() { return ['feed','saved','applied','dismissed'].includes(this.tab); },
    tabLabel() {
      const all = [...this.jobsNav, ...this.sysNav].find(n => n.k === this.tab);
      return all ? all.label : this.tab;
    },

    async go(tab) {
      this.tab = tab;
      this.mobileNav = false;
      if (tab === 'sourcesTab') await this.loadSources();
      if (tab === 'profile') await this.loadProfile();
      await this.load();
    },

    async load() {
      this.loading = true;
      const jobsP = this.isJobView()
        ? fetch(`/api/jobs?tab=${this.tab}&sort=${this.sort}&source=${this.source}`).then(r=>r.json()).catch(()=>[])
        : Promise.resolve(this.jobs);
      const [jobs, counts, health, runs, settings, stats, sources, sched, model, notifyState] = await Promise.all([
        jobsP,
        fetch('/api/counts').then(r=>r.json()).catch(()=>({})),
        fetch('/api/health').then(r=>r.json()).catch(()=>[]),
        fetch('/api/runs').then(r=>r.json()).catch(()=>[]),
        fetch('/api/settings').then(r=>r.json()).catch(()=>({score_threshold:70})),
        fetch('/api/stats').then(r=>r.json()).catch(()=>null),
        fetch('/api/sources').then(r=>r.json()).catch(()=>[]),
        fetch('/api/schedule').then(r=>r.json()).catch(()=>null),
        fetch('/api/model').then(r=>r.json()).catch(()=>null),
        fetch('/api/notify').then(r=>r.json()).catch(()=>null),
      ]);
      this.jobs = jobs;
      this.counts = { ...counts };
      this.health = health;
      this.runs = runs || [];
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
    async addSource() {
      if (!this.newSource.name.trim()) { this.showSnack('Name required', 'error'); return; }
      const r = await fetch('/api/sources', {
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

    // ───────── jobs ─────────
    formatJD(text) {
      if (!text) return 'No description available.';
      if (/<(p|ul|ol|li|br|div|h[1-6]|strong|b)\b/i.test(text)) return text;
      const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      const ls = text.split('\n').map(l=>l.trim()).filter(l=>l.length);
      let html = '';
      for (const line of ls) {
        const isH = /^[A-Z][A-Z0-9 &/\-'’()]{3,}$/.test(line) || /^[A-Za-z ]{2,40}:$/.test(line);
        html += isH ? `<h4>${esc(line.replace(/:$/,''))}</h4>` : `<p>${esc(line)}</p>`;
      }
      return html;
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

    async genCoverLetter(job) {
      // Open the modal in a loading state, then fill it when the API returns.
      this.cover = { loading: true, text: '', provider: '',
                     title: job.title, company: job.company };
      try {
        const r = await fetch(`/api/jobs/${job.id}/cover-letter`, { method: 'POST' });
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          throw new Error(err.detail || `HTTP ${r.status}`);
        }
        const data = await r.json();
        this.cover.text = data.text;
        this.cover.provider = data.provider;
      } catch (e) {
        this.cover = null;
        this.showSnack('Cover letter failed: ' + e.message, 'error');
      } finally {
        if (this.cover) this.cover.loading = false;
      }
    },

    async copyCover() {
      try {
        await navigator.clipboard.writeText(this.cover.text);
        this.showSnack('Copied to clipboard');
      } catch { this.showSnack('Copy failed', 'error'); }
    },

    downloadCover() {
      const safe = (this.cover.company || 'company').replace(/[^a-z0-9]+/gi, '_');
      const blob = new Blob([this.cover.text], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `cover_letter_${safe}.txt`;
      a.click();
      URL.revokeObjectURL(url);
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

    async runNow() {
      const r = await fetch('/api/run', { method:'POST' });
      if (r.status === 409) { this.showSnack('Already running', 'error'); return; }
      this.running = true;
      this.blocking = { label: 'Fetching and scoring jobs…' };
      this.poll();
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
    tier(score) {
      if (score >= 80) return { bg:'#1D9E75', fg:'#fff', stripe:'#1D9E75' };
      if (score >= 70) return { bg:'#EDDCB8', fg:'#7A4E0C', stripe:'#B4791A' };
      return { bg:'#E5E7EB', fg:'#374151', stripe:'#E5E7EB' };
    },
    cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); },
    modelShort(m) { return (m || '').replace('qwen2.5:',''); },
    timeAgo(d) { try { const days = Math.floor((Date.now() - new Date(d)) / 8.64e7); return days <= 0 ? 'today' : days + 'd ago'; } catch { return ''; } },
  };
}
