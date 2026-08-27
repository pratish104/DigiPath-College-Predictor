/**
 * DigiPath — User Logic Engine (app.js)
 * Principal Engineer Standard: Vanilla ES6+, Zero Pipeline Noise.
 */

const DigiPathUser = {
    elements: {
        searchInput: document.getElementById('search-query'),
        citySelect: document.getElementById('city-filter'),
        statusSelect: document.getElementById('status-filter'),
        searchBtn: document.getElementById('query-btn'),
        resultsContainer: document.getElementById('results-view')
    },

    async init() {
        console.log("💎 DigiPath UI Engine Initialized");
        await this.loadFilters();
        this.elements.searchBtn.addEventListener('click', () => this.handleSearch());
        this.elements.searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') this.handleSearch();
        });
    },

    async loadFilters() {
        try {
            const res = await fetch('/api/institute/filters');
            const data = await res.json();
            
            data.cities.forEach(c => {
                const opt = new Option(c, c);
                this.elements.citySelect.add(opt);
            });
            
            data.statuses.forEach(s => {
                const opt = new Option(s, s);
                this.elements.statusSelect.add(opt);
            });
        } catch (err) {
            console.error("Filter Load Error:", err);
        }
    },

    async handleSearch() {
        const q = this.elements.searchInput.value;
        const city = this.elements.citySelect.value;
        const status = this.elements.statusSelect.value;

        this.elements.resultsContainer.innerHTML = `<div class="animate-pulse text-emerald-500">>> ANALYZING KNOWLEDGE GRAPH...</div>`;

        try {
            const params = new URLSearchParams({ q, city, status });
            const res = await fetch(`/api/institute/search?${params}`);
            const data = await res.json();

            if (data.match_type === 'none') {
                this.elements.resultsContainer.innerHTML = `<div class="text-red-500">>> NO MATCHES FOUND IN KNOWLEDGE GRAPH.</div>`;
                return;
            }

            if (data.match_type === 'suggestions') {
                this.renderSuggestions(data.suggestions);
            } else {
                this.renderDetail(data.detail);
            }
        } catch (err) {
            this.elements.resultsContainer.innerHTML = `<div class="text-red-500">>> ERROR CONNECTING TO BACKEND PIPELINE.</div>`;
        }
    },

    renderSuggestions(list) {
        let html = `<div class="space-y-4">
            <h3 class="text-emerald-400 font-bold mb-2">>> MULTIPLE NODES DETECTED. SELECT ONE:</h3>`;
        list.forEach(item => {
            html += `
                <div class="border border-emerald-900 p-4 hover:bg-emerald-950 cursor-pointer transition" 
                     onclick="DigiPathUser.fetchExact('${item.dte_code}')">
                    <div class="text-lg font-bold text-white">${item.name}</div>
                    <div class="text-xs text-emerald-600">${item.location} | DTE: ${item.dte_code}</div>
                </div>`;
        });
        html += `</div>`;
        this.elements.resultsContainer.innerHTML = html;
    },

    async fetchExact(dte) {
        const res = await fetch(`/api/institute/search?q=${dte}`);
        const data = await res.json();
        this.renderDetail(data.detail);
    },

    renderDetail(d) {
        const ov = d.system_overview || {};
        const pm = d.placement_matrix || {};
        const acp = d.ai_cutoff_prediction || d.predicted_2026 ? { predicted_2026: d.predicted_2026 } : {};
        const ap = d.academic_protocols || {};
        const al = d.administration_logistics || {};

        const emeraldWidth = acp.predicted_2026 ? `${acp.predicted_2026}%` : '0%';

        this.elements.resultsContainer.innerHTML = `
            <div class="bg-black border border-emerald-500 p-6 font-mono text-sm text-emerald-400 space-y-6">
                <div class="border-b border-emerald-500 pb-2">
                    <h2 class="text-2xl font-bold text-white uppercase">${d.name}</h2>
                    <p class="text-xs text-emerald-600">${d.location} | DTE CODE: ${d.dte_code}</p>
                </div>

                <!-- EMERALD GAUGE: 2026 PREDICTION -->
                <div class="space-y-1">
                    <div class="flex justify-between text-xs uppercase font-bold">
                        <span>AI Predicted 2026 Cutoff</span>
                        <span>${acp.predicted_2026 || 'N/A'}%</span>
                    </div>
                    <div class="w-full bg-emerald-900/30 h-2 border border-emerald-800">
                        <div class="bg-emerald-500 h-full shadow-[0_0_10px_#10b981]" style="width: ${emeraldWidth}"></div>
                    </div>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <!-- SUB-MATRIX 1: SYSTEM OVERVIEW -->
                    <div class="border border-emerald-800 p-3 bg-emerald-950/10">
                        <h4 class="text-white border-b border-emerald-800 mb-2 font-bold uppercase">System Overview</h4>
                        <ul class="space-y-1 text-xs">
                            <li><span class="text-emerald-600">STATUS:</span> ${ov.status || 'N/A'}</li>
                            <li><span class="text-emerald-600">AUTONOMY:</span> ${ov.autonomy || 'N/A'}</li>
                            <li><span class="text-emerald-600">UNIVERSITY:</span> ${ov.university || 'N/A'}</li>
                            <li><span class="text-emerald-600">ESTABLISHED:</span> ${ov.year_est || 'N/A'}</li>
                        </ul>
                    </div>

                    <!-- SUB-MATRIX 2: PLACEMENT MATRIX -->
                    <div class="border border-emerald-800 p-3 bg-emerald-950/10">
                        <h4 class="text-white border-b border-emerald-800 mb-2 font-bold uppercase">Placement Matrix</h4>
                        <ul class="space-y-1 text-xs">
                            <li><span class="text-emerald-600">AVG PACKAGE:</span> ${pm.average_package || 'N/A'}</li>
                            <li><span class="text-emerald-600">HIGHEST:</span> ${pm.highest_package || 'N/A'}</li>
                            <li class="pt-1"><span class="text-emerald-600">TOP RECRUITERS:</span></li>
                            <li class="text-white opacity-80">${(pm.top_recruiters || []).join(', ') || 'Processing...'}</li>
                        </ul>
                    </div>

                    <!-- SUB-MATRIX 3: ACADEMIC PROTOCOLS -->
                    <div class="border border-emerald-800 p-3 bg-emerald-950/10">
                        <h4 class="text-white border-b border-emerald-800 mb-2 font-bold uppercase">Academic Protocols</h4>
                        <div class="flex flex-wrap gap-2 mt-2">
                            ${Object.keys(ap.branches || {}).map(b => `<span class="px-2 py-0.5 border border-emerald-700 text-[10px] uppercase">${b} (${ap.branches[b]})</span>`).join('') || 'N/A'}
                        </div>
                    </div>

                    <!-- SUB-MATRIX 4: ADMINISTRATION & LOGISTICS -->
                    <div class="border border-emerald-800 p-3 bg-emerald-950/10">
                        <h4 class="text-white border-b border-emerald-800 mb-2 font-bold uppercase">Logistics</h4>
                        <ul class="space-y-1 text-xs">
                            <li><span class="text-emerald-600">FEES (EST):</span> ${al.estimated_open_fees || 'N/A'}</li>
                            <li><span class="text-emerald-600">HOSTEL:</span> ${al.hostel_facility || 'N/A'}</li>
                            <li><span class="text-emerald-600">CAMPUS:</span> ${al.campus_area || 'N/A'}</li>
                        </ul>
                    </div>
                </div>
            </div>
        `;
    }
};

document.addEventListener('DOMContentLoaded', () => DigiPathUser.init());
