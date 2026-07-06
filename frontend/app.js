function jobpilot() {
  return {
    tab: 'feed', jobs: [], counts: {}, health: [], stats: null, loading: true, q: '', detail: null,
    running: false, lastRun: null, threshold: 70,
    statuses: ['surfaced', 'saved', 'applied', 'interview', 'offer', 'rejected', 'dismissed'],
    jobsNav: [
      { k: 'feed', label: 'Feed', icon: 'ti-inbox' },
      { k: 'saved', label: 'Saved', icon: 'ti-bookmark' },
      { k: 'applied', label: 'Applied', icon: 'ti-send' },
      { k: 'dismissed', label: 'Dismissed', icon: 'ti-archive' },
    ],
    sysNav: [
      { k: 'stats', label: 'Stats', icon: 'ti-chart-bar' },
      { k: 'admin', label: 'Admin', icon: 'ti-activity-heartbeat' },
      { k: 'settings', label: 'Settings', icon: 'ti-settings' },
    ],

    isJobView() { return ['feed', 'saved', 'applied', 'dismissed'].includes(this.tab); },

    async load() {
      this.loading = true;
      const jobsP = this.isJobView()
        ? fetch(`/api/jobs?tab=${this.tab}`).then(r => r.json()).catch(() => [])
        : Promise.resolve(this.jobs);
      const [jobs, counts, health, settings, stats] = await Promise.all([
        jobsP,
        fetch('/api/counts').then(r => r.json()).catch(() => ({})),
        fetch('/api/health').then(r => r.json()).catch(() => []),
        fetch('/api/settings').then(r => r.json()).catch(() => ({ score_threshold: 70 })),
        fetch('/api/stats').then(r => r.json()).catch(() => null),
      ]);
      this.jobs = jobs;
      this.counts = { ...counts };
      this.health = health;
      this.threshold = settings.score_threshold ?? 70;
      this.stats = stats;
      this.loading = false;
    },

    formatJD(text) {
      if (!text) return 'No description available.';
      if (/<(p|ul|ol|li|br|div|h[1-6]|strong|b)\b/i.test(text)) return text;
      const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      const lines = text.split('\n').map(l => l.trim()).filter(l => l.length);
      let html = '';
      for (const line of lines) {
        const isH = /^[A-Z][A-Z0-9 &/\-'’()]{3,}$/.test(line) || /^[A-Za-z ]{2,40}:$/.test(line);
        html += isH ? `<h4>${esc(line.replace(/:$/, ''))}</h4>` : `<p>${esc(line)}</p>`;
      }
      return html;
    },

    filtered() {
      if (!this.q.trim()) return this.jobs;
      const s = this.q.toLowerCase();
      return this.jobs.filter(j =>
        (j.title || '').toLowerCase().includes(s) ||
        (j.company || '').toLowerCase().includes(s) ||
        (j.rationale || '').toLowerCase().includes(s));
    },

    openDetail(job) { this.detail = job; },

    async setStatus(job, status) {
      await fetch(`/api/jobs/${job.id}/status`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      this.load();
    },

    async runNow() {
      const r = await fetch('/api/run', { method: 'POST' });
      if (r.status === 409) { alert('Already running'); return; }
      this.running = true;
      this.poll();
    },

    poll() {
      const iv = setInterval(async () => {
        const s = await fetch('/api/run/status').then(r => r.json());
        this.running = s.running;
        this.lastRun = s.last_run;
        if (!s.running) { clearInterval(iv); this.load(); }
      }, 2000);
    },

    async saveThreshold() {
      this.clampThreshold();
      await fetch('/api/settings/threshold', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: this.threshold }),
      });
      await this.load();
    },

    clampThreshold() {
      if (this.threshold === '' || isNaN(this.threshold)) this.threshold = 70;
      this.threshold = Math.max(0, Math.min(100, Math.round(this.threshold)));
    },

    tier(score) {
      if (score >= 80) return { bg: '#1D9E75', fg: '#fff', stripe: '#1D9E75' };
      if (score >= 70) return { bg: '#EDDCB8', fg: '#7A4E0C', stripe: '#B4791A' };
      return { bg: '#E5E7EB', fg: '#374151', stripe: '#E5E7EB' };
    },

    cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); },
    timeAgo(d) { try { const days = Math.floor((Date.now() - new Date(d)) / 8.64e7); return days <= 0 ? 'today' : days + 'd ago'; } catch { return ''; } },
  };
}
