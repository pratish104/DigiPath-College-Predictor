/**
 * DigiPath — System Administrative Dashboard Engine (admin.js)
 * Principal Engineer Standard: High Logic Density, Live Telemetry.
 */

const DigiPathAdmin = {
    elements: {
        feedbackFeed: document.getElementById('feedback-feed'),
        securityFeed: document.getElementById('security-feed'),
        healthGauges: document.getElementById('health-gauges'),
        refreshBtn:   document.getElementById('refresh-telemetry')
    },

    async init() {
        console.log("🛠️ DigiPath Admin Engine Initialized");
        this.sync();
        this.elements.refreshBtn?.addEventListener('click', () => this.sync());
        // Auto-refresh every 30 seconds
        setInterval(() => this.sync(), 30000);
    },

    async sync() {
        console.log("📡 Synchronizing Telemetry...");
        try {
            const [feedback, reports, health] = await Promise.all([
                fetch('/api/admin/feedback').then(r => r.json()),
                fetch('/api/admin/reports').then(r => r.json()),
                fetch('/api/admin/system-health').then(r => r.json())
            ]);

            this.renderHealth(health);
            this.renderFeedback(feedback.items);
            this.renderSecurity(reports.items);
        } catch (err) {
            console.error("Sync Failure:", err);
        }
    },

    renderHealth(data) {
        const m = data.metrics;
        const html = `
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 p-4 border border-emerald-900 bg-black">
                ${this.gauge('BACKEND COVERAGE', m.backend_coverage, 'emerald')}
                ${this.gauge('API SYNC', m.api_sync, 'blue')}
                ${this.gauge('COMMUNITY COHESION', m.community_cohesion, 'yellow')}
                ${this.gauge('NODE CONNECTIVITY', m.node_connectivity, 'red')}
            </div>
            <div class="mt-2 text-[10px] text-emerald-700 flex justify-between uppercase">
                <span>GRAPHIFY STATE: ${data.graphify_state}</span>
                <span>NODES: ${m.nodes} | EDGES: ${m.edges} | COMMUNITIES: ${m.communities}</span>
                <span>LAST SYNC: ${new Date(data.timestamp).toLocaleTimeString()}</span>
            </div>
        `;
        this.elements.healthGauges.innerHTML = html;
    },

    gauge(label, val, color) {
        const colors = {
            emerald: 'bg-emerald-500 shadow-[0_0_8px_#10b981]',
            blue:    'bg-blue-500 shadow-[0_0_8px_#3b82f6]',
            yellow:  'bg-yellow-500 shadow-[0_0_8px_#f59e0b]',
            red:     'bg-red-500 shadow-[0_0_8px_#ef4444]'
        };
        return `
            <div class="space-y-1">
                <div class="flex justify-between text-[10px] font-bold text-gray-500">
                    <span>${label}</span>
                    <span>${val}%</span>
                </div>
                <div class="w-full bg-gray-900 h-1.5 border border-gray-800">
                    <div class="${colors[color]} h-full transition-all duration-1000" style="width: ${val}%"></div>
                </div>
            </div>
        `;
    },

    renderFeedback(items) {
        this.elements.feedbackFeed.innerHTML = items.map(i => `
            <div class="border-l-2 border-emerald-800 pl-3 py-2 bg-emerald-950/5 mb-3">
                <div class="flex justify-between items-start">
                    <span class="text-xs text-emerald-600 font-bold uppercase">${new Date(i.timestamp).toLocaleTimeString()}</span>
                    <span class="text-yellow-500 text-xs">${'★'.repeat(i.rating)}</span>
                </div>
                <p class="text-sm text-white mt-1">${i.message}</p>
                ${i.target_college ? `<div class="text-[10px] text-emerald-800 mt-1">@ ${i.target_college}</div>` : ''}
            </div>
        `).join('') || '<div class="text-gray-700 text-xs italic">No feedback entries.</div>';
    },

    renderSecurity(items) {
        this.elements.securityFeed.innerHTML = items.map(i => `
            <div class="border border-red-900/30 p-3 bg-red-950/5 mb-3">
                <div class="flex justify-between items-center mb-1">
                    <span class="px-1.5 py-0.5 rounded text-[9px] font-bold ${i.severity === 'CRITICAL' ? 'bg-red-600 text-white' : 'bg-yellow-600 text-black'} uppercase">${i.severity}</span>
                    <span class="text-[10px] text-red-800">${i.status}</span>
                </div>
                <div class="text-xs text-white font-bold mb-1">${i.type}</div>
                <p class="text-[11px] text-gray-400 leading-tight">${i.details}</p>
            </div>
        `).join('') || '<div class="text-gray-700 text-xs italic">System secure. No active alerts.</div>';
    }
};

document.addEventListener('DOMContentLoaded', () => DigiPathAdmin.init());
