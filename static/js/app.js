/**
 * DigiPath — Comprehensive UI & Knowledge Matrix Controller (app.js)
 * Production Grade Standard: ES6+, Strict Error Handling, Dual-Pathway Prediction,
 * Resume AI Authentication Session Integration, Multi-Format Report Generation,
 * Dynamic Topbar & Sidebar Role Authorization, and Client-Side Route Enforcement.
 */

const DigiPathApp = {
    // ── 1. Authentication & Session Management ──────────────────────────────
    auth: {
        /**
         * Extract JWT Access Token from localStorage, sessionStorage, or document.cookie.
         * @returns {string|null} Raw JWT token without "Bearer " prefix, or null.
         */
        getToken() {
            let token = localStorage.getItem('access_token') || sessionStorage.getItem('access_token');
            if (token) {
                return token.replace(/^Bearer\s+/i, '').trim();
            }

            // Fallback: parse document.cookie for access_token
            const cookies = document.cookie.split(';');
            for (let c of cookies) {
                const eqIdx = c.indexOf('=');
                if (eqIdx === -1) continue;
                const name = c.substring(0, eqIdx).trim();
                const val  = c.substring(eqIdx + 1).trim();
                if (name === 'access_token' && val) {
                    const decoded = decodeURIComponent(val);
                    return decoded.replace(/^Bearer\s+/i, '').trim();
                }
            }
            return null;
        },

        /**
         * Store token in localStorage, sessionStorage, and document.cookie.
         * @param {string} token - JWT token (with or without "Bearer " prefix).
         */
        setToken(token) {
            if (!token) return;
            const cleanToken = token.replace(/^Bearer\s+/i, '').trim();
            localStorage.setItem('access_token', cleanToken);
            sessionStorage.setItem('access_token', cleanToken);
            document.cookie = `access_token=Bearer ${cleanToken}; path=/; max-age=86400; SameSite=Lax`;
        },

        /**
         * Clear token from all client storage and cookie.
         */
        clearToken() {
            localStorage.removeItem('access_token');
            sessionStorage.removeItem('access_token');
            document.cookie = 'access_token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT; SameSite=Lax';
        },

        /**
         * Build standard headers object including Bearer token when available.
         * @param {Object} customHeaders - Additional headers to merge.
         * @returns {Object} Headers object ready for fetch().
         */
        getAuthHeaders(customHeaders = {}) {
            const token = this.getToken();
            const headers = { ...customHeaders };
            if (token) {
                headers['Authorization'] = `Bearer ${token}`;
            }
            return headers;
        },

        /**
         * Check if an active session or token exists.
         * @returns {boolean}
         */
        isAuthenticated() {
            return Boolean(this.getToken());
        },

        /**
         * Fetch current authenticated user profile from the backend.
         * @returns {Promise<Object|null>} User object or null on failure.
         */
        async getCurrentUser() {
            try {
                const res = await fetch('/api/auth/me', {
                    method: 'GET',
                    headers: this.getAuthHeaders(),
                    credentials: 'include'
                });
                if (!res.ok) return null;
                return await res.json();
            } catch (err) {
                console.warn('⚠️ [AUTH] Failed to fetch current user:', err);
                return null;
            }
        },

        /**
         * Log out user: call server logout, clear local token, redirect to /login.
         */
        async logout() {
            try {
                await fetch('/api/auth/logout', {
                    method: 'POST',
                    headers: this.getAuthHeaders(),
                    credentials: 'include'
                });
            } catch (e) {
                console.warn('⚠️ [AUTH] Logout server call error:', e);
            } finally {
                this.clearToken();
                window.location.href = '/login';
            }
        }
    },

    // ── 2. Resume AI Analyzer Engine ────────────────────────────────────────
    resume: {
        /**
         * Upload and analyze resume document (PDF / DOCX) with session credentials.
         * @param {File|FormData} fileOrFormData - File object or FormData payload.
         * @returns {Promise<Object>} Analysis JSON result from the neural engine.
         */
        async uploadAndAnalyze(fileOrFormData) {
            let formData;
            if (fileOrFormData instanceof FormData) {
                formData = fileOrFormData;
            } else if (fileOrFormData instanceof File) {
                formData = new FormData();
                formData.append('file', fileOrFormData);
            } else {
                throw new Error('Invalid resume file provided for analysis.');
            }

            const token = DigiPathApp.auth.getToken();
            const headers = {};
            if (token) {
                headers['Authorization'] = `Bearer ${token}`;
            }

            console.log('⚡ [RESUME_AI] Submitting resume payload for neural analysis...');
            const res = await fetch('/api/resume/analyze', {
                method: 'POST',
                headers: headers,
                credentials: 'include',
                body: formData
            });

            if (!res.ok) {
                let errMessage = `HTTP ${res.status}`;
                try {
                    const errData = await res.json();
                    errMessage = errData.detail || errData.message || errMessage;
                } catch (e) {
                    const text = await res.text().catch(() => '');
                    if (text) errMessage = text;
                }
                throw new Error(errMessage);
            }

            return await res.json();
        },

        /**
         * Retrieve historical resume analyses for the logged-in user.
         * @returns {Promise<Object>} Resume analysis history.
         */
        async getHistory() {
            const res = await fetch('/api/resume/history', {
                method: 'GET',
                headers: DigiPathApp.auth.getAuthHeaders(),
                credentials: 'include'
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        },

        /**
         * Tailor a resume to a specific job description.
         * @param {string} resumeText - Resume plaintext
         * @param {string} jobDescription - Target job description
         */
        async tailorResume(resumeText, jobDescription) {
            const res = await fetch('/api/resume/tailor', {
                method: 'POST',
                headers: DigiPathApp.auth.getAuthHeaders({ 'Content-Type': 'application/json' }),
                credentials: 'include',
                body: JSON.stringify({ resume_text: resumeText, job_description: jobDescription })
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${res.status}`);
            }
            return await res.json();
        }
    },

    // ── 3. Admission Predictor Controller ───────────────────────────────────
    predictor: {
        /**
         * Run deterministic admission prediction for CET or Diploma.
         * @param {Object} params - { percentile/percentage, category, branch, city, college_type, seat_type, quota, pathway }
         * @returns {Promise<Object>} API response payload with results array.
         */
        async runPrediction(params) {
            const pathway = String(params.pathway || 'fe').toLowerCase();
            const endpoint = (pathway === 'dse' || pathway === 'diploma') ? '/api/predict/diploma' : '/api/predict/cet';

            const payload = {
                category: params.category || 'OPEN',
                branch: params.branch || 'All',
                city: params.city || 'All',
                college_type: params.college_type || 'All',
                seat_type: params.seat_type || 'All',
                quota: params.quota || 'All',
                extra_filters: params.extra_filters || null,
            };

            if (pathway === 'dse' || pathway === 'diploma') {
                payload.percentage = parseFloat(params.percentage || params.score || 0);
            } else {
                payload.percentile = parseFloat(params.percentile || params.score || 0);
            }

            const res = await fetch(endpoint, {
                method: 'POST',
                headers: DigiPathApp.auth.getAuthHeaders({ 'Content-Type': 'application/json' }),
                credentials: 'include',
                body: JSON.stringify(payload)
            });

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || `Prediction failed with status ${res.status}`);
            }

            return await res.json();
        },

        /**
         * Render right-panel college cards with client-side pagination.
         * All zone data is stored in JS memory; 15 cards rendered per page per zone.
         * PREV/NEXT controls update that zone only — no API re-fetch on page change.
         * @param {Object|Array} data - Prediction response payload or raw array.
         * @param {string|HTMLElement} target - Container ID or HTMLElement.
         */
        renderResults(data, target = 'results') {
            const container = typeof target === 'string' ? document.getElementById(target) : target;
            if (!container) return;

            const safe_zone   = data.safe_zone   || (Array.isArray(data) ? data.filter(r => r.status === 'SAFE')    : (data.results ? data.results.filter(r => r.status === 'SAFE')    : []));
            const target_zone = data.target_zone  || (Array.isArray(data) ? data.filter(r => r.status === 'MODERATE' || r.classification === 'Moderate') : (data.results ? data.results.filter(r => r.status === 'MODERATE' || r.classification === 'Moderate') : []));
            const dream_zone  = data.dream_zone   || (Array.isArray(data) ? data.filter(r => r.status === 'DREAM')   : (data.results ? data.results.filter(r => r.status === 'DREAM')   : []));
            const totalFound  = data.total_found != null ? data.total_found : (safe_zone.length + target_zone.length + dream_zone.length);

            if (totalFound === 0) {
                container.innerHTML = `
                    <div style="text-align:center; padding:50px 20px; border:1px solid #d500f9; color:#d500f9;">
                        <div style="font-family:'Orbitron',sans-serif; font-size:14px; letter-spacing:1.5px; margin-bottom:8px;">[ NO_MATCHES_FOUND ]</div>
                        <div style="font-size:11px; color:#6b7280;">No institutes matched your exact filter combination. Try selecting "All Branches" or "All Cities".</div>
                    </div>`;
                return;
            }

            const zones = [
                { key: 'target', label: '[ TARGET_ZONE / MODERATE — Competitive Admission Match ]', color: '#FFB300', chipColor: '#FFB300', items: target_zone },
                { key: 'safe',   label: '[ SAFE_ZONE — High Admission Probability ]',                color: '#00FF66', chipColor: '#00FF66', items: safe_zone   },
                { key: 'dream',  label: '[ DREAM_ZONE / REACH — High Cutoff Targets ]',              color: '#FF3366', chipColor: '#FF3366', items: dream_zone  }
            ];

            // Build skeleton HTML — cards + pagination divs injected per zone
            let html = `
                <div style="font-size:12px; color:#00e5ff; margin-bottom:16px; display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #064e3b; padding-bottom:10px;">
                    <span>FOUND: <strong style="color:#00ff66">${totalFound}</strong> RECOMMENDATIONS &nbsp;(SAFE: ${safe_zone.length} | TARGET: ${target_zone.length} | DREAM: ${dream_zone.length})</span>
                    <span style="color:#6b7280; font-size:10px; letter-spacing:1px;">AI_SORTED_BY_PROBABILITY</span>
                </div>`;

            for (const z of zones) {
                if (z.items.length === 0) continue;
                html += `
                    <div style="margin-top:26px; border-left:3px solid ${z.color}; padding-left:14px; margin-bottom:14px;">
                        <div style="color:${z.color}; font-family:'Orbitron',sans-serif; font-size:15px; letter-spacing:1.5px; font-weight:700;">${z.label} (${z.items.length})</div>
                    </div>
                    <div id="${z.key}_cards" style="border:1px solid ${z.color}44; padding:4px 4px 0 4px; margin-bottom:6px;"></div>
                    <div id="${z.key}_pagination" class="flex justify-between items-center mt-3 text-xs mono"
                         style="display:flex; justify-content:space-between; align-items:center; margin-bottom:24px; font-size:11px; font-family:'Share Tech Mono',monospace;"></div>`;
            }

            container.innerHTML = html;

            // Seed pagination state and render page 1 for every active zone
            DigiPathApp.predictor._paginationState = {};
            for (const z of zones) {
                if (z.items.length === 0) continue;
                DigiPathApp.predictor._paginationState[z.key] = {
                    items: z.items, color: z.color, chipColor: z.chipColor, currentPage: 1, pageSize: 15
                };
                DigiPathApp.predictor._renderPage(z.key);
            }
        },

        /**
         * Render a single page of cards for the given zone.
         * Called on load (page 1) and on every PREV / NEXT click.
         * @param {string} zoneKey - 'safe' | 'target' | 'dream'
         */
        _renderPage(zoneKey) {
            const state = DigiPathApp.predictor._paginationState[zoneKey];
            if (!state) return;
            const { items, color, chipColor, currentPage, pageSize } = state;
            const totalPages  = Math.max(1, Math.ceil(items.length / pageSize));
            const startIdx    = (currentPage - 1) * pageSize;
            const pageItems   = items.slice(startIdx, startIdx + pageSize);

            const cardsEl = document.getElementById(`${zoneKey}_cards`);
            const pageEl  = document.getElementById(`${zoneKey}_pagination`);
            if (!cardsEl || !pageEl) return;

            cardsEl.innerHTML = pageItems.map((item, i) => {
                const prevVal     = (item.prev_cutoff     != null ? item.prev_cutoff     : item.previous_cutoff) ?? '—';
                const forecastVal = (item.forecast_2026   != null ? item.forecast_2026   : (item.predicted_2026 || item.predicted_cutoff)) ?? prevVal;
                const probVal     = (item.probability     != null ? item.probability     : (item.probability_percent ?? 50));
                const accentColor = item.accent_color || chipColor;
                const rankNum     = item.rank || (startIdx + i + 1);
                return `
                    <div class="result-card" style="background:rgba(3,7,18,0.95);border:1px solid #064e3b;padding:20px;margin-bottom:16px;position:relative;display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:16px;transition:border-color 0.2s;" onmouseenter="this.style.borderColor='${accentColor}'" onmouseleave="this.style.borderColor='#064e3b'">
                        <div class="corner corner-tl"></div><div class="corner corner-tr"></div>
                        <div class="corner corner-bl"></div><div class="corner corner-br"></div>
                        <div style="position:absolute;top:12px;right:14px;font-size:12px;color:#00aa2b;font-weight:700;font-family:'Orbitron',sans-serif;">#RANK_${rankNum}</div>
                        <div style="flex:2;min-width:280px;">
                            <div style="font-size:16px;font-weight:600;color:#00ff66;margin-bottom:8px;line-height:1.3;text-shadow:0 0 8px rgba(0,255,102,0.25);">${item.college_name || 'Maharashtra Institute'}</div>
                            <div style="display:flex;flex-wrap:wrap;gap:8px;font-size:11px;">
                                <span style="background:rgba(0,229,255,0.1);color:#00e5ff;padding:2px 8px;border:1px solid rgba(0,229,255,0.3);">DTE: ${item.dte_code || item.college_code || '—'}</span>
                                <span style="background:rgba(0,255,102,0.08);color:#00ff66;padding:2px 8px;border:1px solid rgba(0,255,102,0.2);">BRANCH: ${item.branch || '—'}</span>
                                <span style="background:rgba(255,179,0,0.1);color:#ffb300;padding:2px 8px;border:1px solid rgba(255,179,0,0.25);">TYPE: ${item.college_type || 'Un-Aided'}</span>
                                <span style="color:#00e5ff;">LOC: ${item.location || item.city || 'Maharashtra'}</span>
                            </div>
                        </div>
                        <div style="display:flex;gap:28px;align-items:center;flex:1;justify-content:flex-end;min-width:290px;">
                            <div style="text-align:center;">
                                <div style="font-family:'Orbitron',sans-serif;font-size:22px;font-weight:700;color:#00e5ff;text-shadow:0 0 10px rgba(0,229,255,0.4);line-height:1;margin-bottom:5px;">${prevVal}</div>
                                <div style="font-size:9px;color:#6b7280;letter-spacing:1px;">PREV_CUTOFF</div>
                            </div>
                            <div style="text-align:center;">
                                <div style="font-family:'Orbitron',sans-serif;font-size:22px;font-weight:700;color:#d500f9;text-shadow:0 0 10px rgba(213,0,249,0.4);line-height:1;margin-bottom:5px;">${forecastVal}</div>
                                <div style="font-size:9px;color:#6b7280;letter-spacing:1px;">2026_FORECAST</div>
                            </div>
                            <div style="text-align:center;min-width:85px;">
                                <div style="font-family:'Orbitron',sans-serif;font-size:16px;font-weight:700;padding:5px 12px;border:1px solid ${accentColor};color:${accentColor};box-shadow:0 0 10px ${accentColor}33;border-radius:4px;display:inline-block;">${probVal}%</div>
                                <div style="font-size:9px;color:#6b7280;letter-spacing:1px;margin-top:5px;">PROBABILITY</div>
                            </div>
                        </div>
                    </div>`;
            }).join('');

            // Nav controls — grey-out buttons at limits
            const prevOff = currentPage <= 1;
            const nextOff = currentPage >= totalPages;
            const navBtn  = (label, disabled, delta) => `
                <button onclick="DigiPathApp.predictor._changePage('${zoneKey}',${delta})"
                    style="padding:7px 16px;border:1px solid ${disabled?'#1f2937':color};color:${disabled?'#374151':color};
                    background:transparent;font-family:'Share Tech Mono',monospace;font-size:11px;letter-spacing:1.5px;
                    cursor:${disabled?'not-allowed':'pointer'};transition:all 0.15s;"
                    ${disabled?'disabled':''}>${label}</button>`;
            pageEl.innerHTML = `
                ${navBtn('◄ PREV', prevOff, -1)}
                <span style="color:#9ca3af;letter-spacing:2px;font-size:11px;">PAGE ${currentPage} OF ${totalPages} &nbsp;|&nbsp; ${items.length} RESULTS</span>
                ${navBtn('NEXT ►', nextOff, +1)}`;
        },

        /**
         * Advance or rewind a zone's page and re-render it.
         * @param {string} zoneKey
         * @param {number} delta - +1 (next) or -1 (prev)
         */
        _changePage(zoneKey, delta) {
            const state = DigiPathApp.predictor._paginationState[zoneKey];
            if (!state) return;
            const totalPages  = Math.max(1, Math.ceil(state.items.length / state.pageSize));
            state.currentPage = Math.max(1, Math.min(totalPages, state.currentPage + delta));
            DigiPathApp.predictor._renderPage(zoneKey);
            // Smooth-scroll to top of this zone
            const el = document.getElementById(`${zoneKey}_cards`);
            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        },

        /** In-memory pagination state keyed by zone ('safe', 'target', 'dream'). */
        _paginationState: {},
    },



    // ── 4. Prediction Reports & Exports ─────────────────────────────────────
    reports: {
        /**
         * Download prediction report in PDF, CSV, Excel, or JSON format.
         * @param {string} format  - 'pdf', 'csv', 'xlsx', 'excel', or 'json'.
         * @param {Object} params  - Query params (percentile, category, branch, pathway, city, college_type).
         */
        async downloadPredictionReport(format = 'pdf', params = null) {
            const cleanFormat = (format || 'pdf').toLowerCase().replace('excel', 'xlsx');
            const searchParams = new URLSearchParams();

            if (params && typeof params === 'object') {
                if (params.percentile  != null) searchParams.append('percentile',   params.percentile);
                if (params.percentage  != null) searchParams.append('percentage',   params.percentage);
                if (params.score       != null) searchParams.append('score',        params.score);
                if (params.category)            searchParams.append('category',     params.category);
                if (params.branch && params.branch !== 'All') searchParams.append('branch', params.branch);
                if (params.city   && params.city   !== 'All') searchParams.append('city',   params.city);
                if (params.college_type && params.college_type !== 'All') searchParams.append('college_type', params.college_type);
                if (params.pathway)             searchParams.append('pathway',      params.pathway);
            }

            const queryString = searchParams.toString();
            const endpoint    = `/api/predict/report/${cleanFormat}${queryString ? '?' + queryString : ''}`;

            console.log(`📥 [REPORT] Initiating stream download: ${endpoint}`);

            try {
                const res = await fetch(endpoint, {
                    method: 'GET',
                    headers: DigiPathApp.auth.getAuthHeaders(),
                    credentials: 'include'
                });

                if (!res.ok) {
                    throw new Error(`Report export failed with status ${res.status}`);
                }

                // Extract filename from Content-Disposition header if available
                let filename = `DigiPath_Report_${Date.now()}.${cleanFormat === 'xlsx' ? 'xlsx' : cleanFormat}`;
                const disposition = res.headers.get('Content-Disposition');
                if (disposition && disposition.includes('filename=')) {
                    const match = disposition.match(/filename="?([^"]+)"?/);
                    if (match && match[1]) filename = match[1];
                }

                const blob = await res.blob();
                const url  = window.URL.createObjectURL(blob);
                const a    = document.createElement('a');
                a.style.display = 'none';
                a.href     = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                setTimeout(() => {
                    document.body.removeChild(a);
                    window.URL.revokeObjectURL(url);
                }, 200);

            } catch (err) {
                console.error('❌ [REPORT] Download error:', err);
                window.location.href = endpoint;
            }
        }
    },

    // ── 4. Institute Knowledge Matrix Controller ────────────────────────────
    elements: {
        searchInput:   null,
        citySelect:    null,
        typeSelect:    null,
        searchBtn:     null,
        gridContainer: null,
        matchCount:    null,
        modal:         null,
        modalContent:  null
    },

    bindDomElements() {
        this.elements = {
            searchInput:   document.getElementById('instSearch')    || document.getElementById('search-query'),
            citySelect:    document.getElementById('cityFilter')    || document.getElementById('city-filter'),
            typeSelect:    document.getElementById('typeFilter')    || document.getElementById('status-filter'),
            searchBtn:     document.getElementById('searchBtn')     || document.getElementById('query-btn'),
            gridContainer: document.getElementById('instGrid')      || document.getElementById('results-view'),
            matchCount:    document.getElementById('matchCount'),
            modal:         document.getElementById('detailModal'),
            modalContent:  document.getElementById('modalContent')
        };
    },

    /**
     * Main initialization method.
     * Runs auth route guard, binds DOM, and bootstraps institute search if elements are present.
     */
    async init() {
        console.log('💎 [DIGIPATH_UI] Initializing Neural Core...');

        // ── Mandatory Client-Side Authentication Route Guard ──────────────
        const protectedPaths = [
            '/profile',
            '/dashboard',
            '/resume-analyzer',
            '/resume-builder',
            '/predictor',
            '/job-recommender',
            '/roadmap'
        ];
        const adminPaths = [
            '/admin-dashboard',
            '/admin',
            '/admin_dashboard',
            '/admin_panel'
        ];

        const currentPath = window.location.pathname.toLowerCase().replace(/\/+$/, '') || '/';
        const isProtected = protectedPaths.some(p => currentPath === p || currentPath.startsWith(p + '/'));
        const isAdminRoute = adminPaths.some(p => currentPath === p || currentPath.startsWith(p + '/'));

        if ((isProtected || isAdminRoute) && !DigiPathApp.auth.isAuthenticated()) {
            console.warn(`🔒 [AUTH_GUARD] Protected route '${window.location.pathname}' requires authentication. Redirecting to login.`);
            const targetNext = window.location.pathname + window.location.search;
            window.location.href = '/login?next=' + encodeURIComponent(targetNext);
            return;
        }

        // Check Admin privilege for /admin routes
        if (isAdminRoute) {
            const currentUser = await DigiPathApp.auth.getCurrentUser();
            const isAdmin = currentUser && (currentUser.is_admin === true || (currentUser.role && currentUser.role.toUpperCase() === 'ADMIN'));
            if (!isAdmin) {
                console.error('⛔ [AUTH_GUARD] 403 FORBIDDEN: Root Credentials Required.');
                document.body.innerHTML = `
                    <div style="background:#000; color:#ff0055; font-family:'Share Tech Mono', monospace; display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; margin:0; text-align:center; padding:20px;">
                        <h1 style="font-family:'Orbitron', sans-serif; font-size:36px; letter-spacing:3px; margin-bottom:8px; text-shadow:0 0 20px #ff0055;">[ 403 FORBIDDEN: ROOT_CREDENTIALS_REQUIRED ]</h1>
                        <p style="color:#ff88a3; font-size:14px; max-width:520px; line-height:1.6; margin-bottom:24px;">&gt; SECURITY_VIOLATION: Level-5 Administrator clearance required to access the Root Command Center. Unauthorized access attempt logged.</p>
                        <a href="/dashboard" style="border:1px solid #ff0055; color:#ff0055; padding:10px 20px; text-decoration:none; font-size:12px; letter-spacing:1.5px;">&lt; RETURN_TO_CANDIDATE_DASHBOARD</a>
                    </div>
                `;
                return;
            }
        }

        this.bindDomElements();

        if (this.elements.gridContainer || this.elements.searchInput) {
            await this.loadFilters();
            this.bindEvents();
            await this.searchInstitutes();
        }
    },

    bindEvents() {
        if (this.elements.searchBtn) {
            this.elements.searchBtn.addEventListener('click', () => this.searchInstitutes());
        }
        if (this.elements.searchInput) {
            this.elements.searchInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') this.searchInstitutes();
            });
        }
        if (this.elements.citySelect) {
            this.elements.citySelect.addEventListener('change', () => this.searchInstitutes());
        }
        if (this.elements.typeSelect) {
            this.elements.typeSelect.addEventListener('change', () => this.searchInstitutes());
        }
    },

    async loadFilters() {
        try {
            const res = await fetch('/api/institute/filters', { credentials: 'include' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();

            if (this.elements.citySelect) {
                this.elements.citySelect.innerHTML =
                    '<option value="ALL">ALL_CITIES</option>' +
                    (data.cities || []).map(c => `<option value="${c}">${c.toUpperCase()}</option>`).join('');
            }

            if (this.elements.typeSelect) {
                this.elements.typeSelect.innerHTML =
                    '<option value="ALL">ALL_STATUSES</option>' +
                    (data.statuses || []).map(s => `<option value="${s}">${s.toUpperCase()}</option>`).join('');
            }
        } catch (err) {
            console.error('❌ [FILTERS] Load error:', err);
        }
    },

    async searchInstitutes() {
        if (!this.elements.gridContainer) return;

        const q      = this.elements.searchInput ? this.elements.searchInput.value.trim() : '';
        const city   = this.elements.citySelect  ? this.elements.citySelect.value          : 'ALL';
        const status = this.elements.typeSelect  ? this.elements.typeSelect.value          : 'ALL';

        this.elements.gridContainer.innerHTML = `
            <div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--neon-green, #00ff41);">
                &gt;&gt; SEARCHING KNOWLEDGE MATRIX &amp; DTE ARCHIVES...
            </div>
        `;

        try {
            const params = new URLSearchParams();
            if (q)                      params.append('q',      q);
            if (city   && city   !== 'ALL') params.append('city',   city);
            if (status && status !== 'ALL') params.append('status', status);

            const res = await fetch(`/api/institute/search?${params.toString()}`, { credentials: 'include' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();

            let records = [];
            if (data.match_type === 'exact' && data.detail) {
                records = [data.detail];
            } else if (data.suggestions) {
                records = data.suggestions;
            }

            if (this.elements.matchCount) {
                this.elements.matchCount.textContent = `RECORDS: ${records.length}`;
            }

            if (records.length === 0) {
                this.elements.gridContainer.innerHTML = `
                    <div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--neon-magenta, #ff0055);">
                        &gt;&gt; NO INSTITUTES FOUND MATCHING SPECIFIED CRITERIA.
                    </div>
                `;
                return;
            }

            this.renderInstituteGrid(records);

        } catch (err) {
            console.error('❌ [SEARCH] Pipeline error:', err);
            this.elements.gridContainer.innerHTML = `
                <div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--neon-magenta, #ff0055);">
                    &gt;&gt; PIPELINE ERROR: ${err.message}
                </div>
            `;
        }
    },

    renderInstituteGrid(records) {
        this.elements.gridContainer.innerHTML = records.map(item => {
            const dte      = String(item.dte_code || '00000').padStart(5, '0');
            const name     = item.name || 'Maharashtra Institute of Technology';
            const loc      = item.city || item.location || 'Maharashtra';
            const status   = item.status || (item.system_overview ? item.system_overview.status   : 'Un-Aided')       || 'Un-Aided';
            const autonomy = item.autonomy || (item.system_overview ? item.system_overview.autonomy : 'Non-Autonomous') || 'Non-Autonomous';
            const naac     = item.naac_grade || (item.system_overview ? item.system_overview.accreditation : 'A Grade') || 'A Grade';

            const avgPackage = item.avg_package || (item.placement_matrix     ? item.placement_matrix.average_package         : '5.5 LPA')            || '5.5 LPA';
            const openFees   = item.open_fees   || (item.administration_logistics ? item.administration_logistics.estimated_open_fees : '₹1,25,000 / Year') || '₹1,25,000 / Year';
            const pred26     = item.predicted_2026 || (item.ai_cutoff_prediction ? item.ai_cutoff_prediction.predicted_2026 : '72.50') || '72.50';

            return `
                <div class="institute-card">
                    <div class="corner corner-tl"></div><div class="corner corner-tr"></div>
                    <div class="corner corner-bl"></div><div class="corner corner-br"></div>

                    <div class="inst-header">
                        <h3 class="inst-name">${name}</h3>
                        <span class="inst-dte">DTE: ${dte}</span>
                    </div>

                    <div class="inst-loc">${loc} // ${status} (${autonomy})</div>

                    <div class="inst-stats">
                        <div class="inst-stat-item">
                            <div class="stat-label">AVG_PACKAGE</div>
                            <div class="stat-val">${avgPackage}</div>
                        </div>
                        <div class="inst-stat-item">
                            <div class="stat-label">NAAC_GRADE</div>
                            <div class="stat-val" style="color: var(--neon-cyan, #00e5ff)">${naac}</div>
                        </div>
                        <div class="inst-stat-item">
                            <div class="stat-label">OPEN_FEES</div>
                            <div class="stat-val">${openFees}</div>
                        </div>
                    </div>

                    <div class="inst-actions">
                        <span style="font-size: 11px; color: var(--neon-cyan, #00e5ff);">2026_PRED: <b>${pred26}%</b></span>
                        <button class="view-btn" onclick="DigiPathApp.openInstituteDetail('${dte}')">VIEW_DETAILS &gt;</button>
                    </div>
                </div>
            `;
        }).join('');
    },

    async openInstituteDetail(dteCode) {
        const modal   = document.getElementById('detailModal');
        const content = document.getElementById('modalContent');
        if (!modal || !content) return;

        content.innerHTML = `<div style="text-align:center; padding: 40px; color: var(--neon-green, #00ff41);">&gt;&gt; RETRIEVING DEEP INTELLIGENCE FOR DTE ${dteCode}...</div>`;
        modal.style.display = 'flex';

        try {
            const res = await fetch(`/api/institute/detail/${dteCode}`, { credentials: 'include' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            const d    = data.detail || {};

            const zcode     = String(d.dte_code || dteCode).padStart(5, '0');
            const branches  = Array.isArray(d.branches) ? d.branches : (d.academic_protocols ? Object.keys(d.academic_protocols.branches || {}) : []);
            const recruiters = Array.isArray(d.top_recruiters) ? d.top_recruiters : (d.placement_matrix ? d.placement_matrix.top_recruiters || [] : []);

            content.innerHTML = `
                <div style="border-bottom: 1px solid var(--border-green, rgba(0,255,65,0.2)); padding-bottom: 16px; margin-bottom: 20px;">
                    <div style="font-size: 11px; color: var(--neon-cyan, #00e5ff); margin-bottom: 4px;">DTE_CODE: ${zcode} | AFFILIATION: ${d.university || 'State Technological University'}</div>
                    <h2 style="font-family: 'Orbitron', sans-serif; font-size: 18px; color: var(--neon-green, #00ff41); line-height: 1.3;">${d.name || 'Institute Profile'}</h2>
                    <div style="font-size: 12px; color: var(--text-mid, #00cc55); margin-top: 4px;">${d.location || d.city || 'Maharashtra'} | Status: ${d.status || 'Un-Aided'} (${d.autonomy || 'Non-Autonomous'})</div>
                </div>

                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px;">
                    <div style="background: rgba(0,255,102,0.04); border: 1px solid rgba(0,255,102,0.15); padding: 14px;">
                        <div style="font-size: 10px; color: var(--text-dim, #008833); text-transform: uppercase;">Placement Intelligence</div>
                        <div style="font-size: 14px; margin-top: 4px; color: var(--neon-green, #00ff41);">Highest: <b>${d.highest_package || '18.0 LPA'}</b></div>
                        <div style="font-size: 14px; color: var(--neon-cyan, #00e5ff);">Average: <b>${d.avg_package || '6.0 LPA'}</b></div>
                        <div style="font-size: 11px; color: var(--text-mid, #00cc55); margin-top: 6px;">Top Recruiters: ${(recruiters || []).slice(0, 5).join(', ') || 'TCS, Infosys, Capgemini, LTI'}</div>
                    </div>

                    <div style="background: rgba(0,229,255,0.04); border: 1px solid rgba(0,229,255,0.15); padding: 14px;">
                        <div style="font-size: 10px; color: var(--text-dim, #008833); text-transform: uppercase;">Campus &amp; Logistics</div>
                        <div style="font-size: 14px; margin-top: 4px; color: var(--neon-green, #00ff41);">Open Fees: <b>${d.open_fees || '₹1,35,000 / Year'}</b></div>
                        <div style="font-size: 14px; color: var(--neon-cyan, #00e5ff);">Hostel: <b>${d.hostel || 'Available (Boys/Girls)'}</b></div>
                        <div style="font-size: 11px; color: var(--text-mid, #00cc55); margin-top: 6px;">Campus Area: ${d.campus_area || '25 Acres'} | Contact: ${d.contact_person || 'Admissions Cell'}</div>
                    </div>
                </div>

                <div style="background: rgba(0,255,102,0.02); border: 1px solid rgba(0,255,102,0.1); padding: 14px; margin-bottom: 20px;">
                    <div style="font-size: 10px; color: var(--text-dim, #008833); text-transform: uppercase; margin-bottom: 6px;">Offered Engineering Programs</div>
                    <div style="display: flex; flex-wrap: wrap; gap: 6px;">
                        ${branches.map(b => `<span style="border: 1px solid rgba(0,255,102,0.3); padding: 3px 8px; font-size: 11px; color: var(--neon-green, #00ff41); background: rgba(0,255,102,0.05);">${b}</span>`).join('') || '<span style="color:#888;">Computer Engineering, IT, AI &amp; Data Science</span>'}
                    </div>
                </div>

                <div style="display: flex; justify-content: space-between; align-items: center; border-top: 1px solid rgba(0,255,102,0.15); padding-top: 14px;">
                    <div style="font-size: 12px; color: var(--neon-cyan, #00e5ff);">
                        2024 Actual: <b>${d.actual_2024 || 'N/A'}%</b> | 2025 Actual: <b>${d.actual_2025 || 'N/A'}%</b> | 2026 Forecast: <b style="color:var(--neon-green, #00ff41);">${d.predicted_2026 || 'N/A'}%</b>
                    </div>
                    <a href="/predictor" class="view-btn" style="text-decoration:none;">RUN_PREDICTOR_ON_COLLEGE &gt;</a>
                </div>
            `;
        } catch (err) {
            content.innerHTML = `<div style="text-align:center; padding: 40px; color: var(--neon-magenta, #ff0055);">&gt;&gt; ERROR FETCHING DETAILS: ${err.message}</div>`;
        }
    }
};

// ── Portal URL Generator Helper ──────────────────────────────────────────────
function generatePortalUrls(jobTitle) {
    const title        = jobTitle || 'Software Engineer';
    const encodedTitle = encodeURIComponent(title);
    const slugTitle    = encodeURIComponent(title.toLowerCase().replace(/[^a-z0-9]+/g, '-'));

    return {
        linkedin:  `https://www.linkedin.com/jobs/search/?keywords=${encodedTitle}&location=Maharashtra`,
        indeed:    `https://in.indeed.com/jobs?q=${encodedTitle}&l=Maharashtra`,
        workindia: `https://www.workindia.in/jobs/${slugTitle}-jobs-in-mumbai/`
    };
}

// ── Global download report helper (used inline in predictor template) ────────
function downloadReport(format) {
    const scoreVal   = document.getElementById('score')       ? parseFloat(document.getElementById('score').value)       : null;
    const catVal     = document.getElementById('category')    ? document.getElementById('category').value                 : null;
    const branchVal  = document.getElementById('branch')      ? document.getElementById('branch').value                   : null;
    const cityVal    = document.getElementById('city')        ? document.getElementById('city').value                     : null;
    const typeVal    = document.getElementById('collegeType') ? document.getElementById('collegeType').value              : null;
    const pathwayVal = (typeof currentPathway !== 'undefined') ? currentPathway : 'fe';

    const params = {
        score:        scoreVal,
        percentile:   scoreVal,
        percentage:   scoreVal,
        category:     catVal,
        branch:       branchVal,
        city:         cityVal,
        college_type: typeVal,
        pathway:      pathwayVal
    };

    DigiPathApp.reports.downloadPredictionReport(format, params);
}

// ── 5. Profile API Controller ────────────────────────────────────────────────
DigiPathApp.profile = {
    async getProfile() {
        const res = await fetch('/api/user/profile', {
            headers:     DigiPathApp.auth.getAuthHeaders(),
            credentials: 'include'
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    },

    async saveProfile(payload) {
        const res = await fetch('/api/user/profile', {
            method:      'POST',
            headers:     DigiPathApp.auth.getAuthHeaders({ 'Content-Type': 'application/json' }),
            credentials: 'include',
            body:        JSON.stringify(payload)
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        return await res.json();
    },

    async changePassword(currentPassword, newPassword) {
        const res = await fetch('/api/user/settings', {
            method:      'POST',
            headers:     DigiPathApp.auth.getAuthHeaders({ 'Content-Type': 'application/json' }),
            credentials: 'include',
            body:        JSON.stringify({ current_password: currentPassword, new_password: newPassword })
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        return await res.json();
    },

    async bookmarkCollege(dteCode, name = '', city = '') {
        const res = await fetch('/api/user/bookmarks/college', {
            method:      'POST',
            headers:     DigiPathApp.auth.getAuthHeaders({ 'Content-Type': 'application/json' }),
            credentials: 'include',
            body:        JSON.stringify({ dte_code: dteCode, name, city })
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    },

    async removeCollegeBookmark(dteCode) {
        const res = await fetch(`/api/user/bookmarks/college/${dteCode}`, {
            method:      'DELETE',
            headers:     DigiPathApp.auth.getAuthHeaders(),
            credentials: 'include'
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    },

    async bookmarkJob(jobId, title = '', company = '') {
        const res = await fetch('/api/user/bookmarks/job', {
            method:      'POST',
            headers:     DigiPathApp.auth.getAuthHeaders({ 'Content-Type': 'application/json' }),
            credentials: 'include',
            body:        JSON.stringify({ job_id: jobId, title, company })
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    },

    async removeJobBookmark(jobId) {
        const res = await fetch(`/api/user/bookmarks/job/${jobId}`, {
            method:      'DELETE',
            headers:     DigiPathApp.auth.getAuthHeaders(),
            credentials: 'include'
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    }
};

// ── 6. Global Topbar User Pill & Dynamic Admin Injections ───────────────────
async function initGlobalTopbar() {
    const pillWrap   = document.getElementById('userPillWrap');
    const loginBtn   = document.getElementById('loginNavBtn');
    const pillBtn    = document.getElementById('userPillBtn');
    const dropdown   = document.getElementById('pillDropdown');
    const initialsEl = document.getElementById('navInitials');
    const nameEl     = document.getElementById('navName');

    const hasLocalToken = DigiPathApp.auth.isAuthenticated();
    if (pillWrap)  pillWrap.style.display  = hasLocalToken ? 'block' : 'none';
    if (loginBtn) loginBtn.style.display   = hasLocalToken ? 'none'  : 'inline-block';

    if (pillBtn && dropdown) {
        pillBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const isOpen = dropdown.classList.contains('open');
            dropdown.classList.toggle('open', !isOpen);
            pillBtn.classList.toggle('open', !isOpen);
            pillBtn.setAttribute('aria-expanded', String(!isOpen));
        });

        pillBtn.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                pillBtn.click();
            }
            if (e.key === 'Escape') {
                dropdown.classList.remove('open');
                pillBtn.classList.remove('open');
                pillBtn.setAttribute('aria-expanded', 'false');
            }
        });

        document.addEventListener('click', (e) => {
            if (pillWrap && !pillWrap.contains(e.target)) {
                dropdown.classList.remove('open');
                pillBtn.classList.remove('open');
                pillBtn.setAttribute('aria-expanded', 'false');
            }
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && dropdown.classList.contains('open')) {
                dropdown.classList.remove('open');
                pillBtn.classList.remove('open');
                pillBtn.setAttribute('aria-expanded', 'false');
            }
        });
    }

    try {
        const user = await DigiPathApp.auth.getCurrentUser();

        if (user && user.email) {
            const initials     = (user.full_name || user.email || 'U').trim().split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase();
            const displayName  = (user.full_name || user.email || 'OPERATOR').split(' ')[0].toUpperCase();

            if (initialsEl) initialsEl.textContent = initials;
            if (nameEl)     nameEl.textContent     = displayName;
            if (pillWrap)   pillWrap.style.display  = 'block';
            if (loginBtn)  loginBtn.style.display   = 'none';

            const isAdmin = user.is_admin === true || (user.role && user.role.toUpperCase() === 'ADMIN');
            if (isAdmin && dropdown && !document.getElementById('dropdownAdminLink')) {
                const adminDropItem = document.createElement('a');
                adminDropItem.id = 'dropdownAdminLink';
                adminDropItem.href = '/admin';
                adminDropItem.style.color = '#ff0055';
                adminDropItem.style.fontWeight = 'bold';
                adminDropItem.innerHTML = '⚡ [ ADMIN_COMMAND ]';
                dropdown.insertBefore(adminDropItem, dropdown.firstChild);
            }

            console.log(`✅ [TOPBAR] Session verified for: ${user.email} (Admin: ${isAdmin})`);
        } else {
            DigiPathApp.auth.clearToken();
            if (pillWrap)  pillWrap.style.display  = 'none';
            if (loginBtn) loginBtn.style.display   = 'inline-block';

            console.warn('⚠️ [TOPBAR] No valid session. Showing login CTA.');
        }
    } catch (e) {
        console.warn('⚠️ [TOPBAR] Could not verify session with backend:', e);
    }
}

// ── DOMContentLoaded Bootstrap ───────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    DigiPathApp.init();
    initGlobalTopbar();
});