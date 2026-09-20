/**
 * DigiPath Unified Application Shell & Expert-Grade Theme Controller (digipath-shell.js)
 * v2 — Premium Cinematic Edition
 *
 * Implements:
 *   • View Transition API radial wipe (origin-aware clip-path expand)
 *   • Manual overlay fallback for browsers without VT API
 *   • Animated icon rotation morph on toggle state change
 *   • Persistent Light / Dark / System with OS media-query synchronisation
 *   • Anti-FOUC initialisation (also reinforced by inline head scripts)
 *   • Accessible radiogroup navigation keyboard support
 *   • DigiPath sidebar navigation injection & profile pod
 */

(function () {
  'use strict';

  // ── 1. DigiPath Theme Engine ──────────────────────────────────────────────
  const DigiPathTheme = (function () {
    const STORAGE_KEY = 'digipath_theme';
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const reducedMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    let transitionInFlight = false;

    function getSavedPreference() {
      try {
        const val = localStorage.getItem(STORAGE_KEY);
        if (val === 'light' || val === 'dark' || val === 'system') return val;
      } catch (_) {}
      return 'system';
    }

    function resolveTheme(pref) {
      if (pref === 'dark')  return 'dark';
      if (pref === 'light') return 'light';
      return mediaQuery.matches ? 'dark' : 'light';
    }

    // ── Cinematic transition helpers ──────────────────────────────────────

    /** Dispatch the actual DOM mutations that change theme */
    function _commitTheme(valid, effective, persist) {
      document.documentElement.setAttribute('data-theme', effective);
      document.documentElement.setAttribute('data-theme-setting', valid);
      document.documentElement.style.colorScheme = effective;

      if (persist) {
        try { localStorage.setItem(STORAGE_KEY, valid); } catch (_) {}
      }

      // Sync toggle UI controls
      document.querySelectorAll('.dp-theme-toggle').forEach(group => {
        group.querySelectorAll('.dp-theme-btn').forEach(btn => {
          const mode = btn.getAttribute('data-mode');
          const isSelected = mode === valid;
          btn.setAttribute('aria-checked', String(isSelected));
          btn.tabIndex = isSelected ? 0 : -1;

          // Animate icon on selection
          const svg = btn.querySelector('svg');
          if (svg) {
            if (isSelected) {
              svg.classList.add('dp-icon-active');
            } else {
              svg.classList.remove('dp-icon-active');
            }
          }
        });
      });

      window.dispatchEvent(new CustomEvent('digipath:themechange', {
        detail: { setting: valid, effectiveTheme: effective }
      }));

      const announcement = document.getElementById('dp-theme-announcement');
      if (announcement) {
        announcement.textContent = valid === 'system'
          ? `System theme selected. Currently using ${effective} mode.`
          : `${valid.charAt(0).toUpperCase() + valid.slice(1)} theme selected.`;
      }
    }

    /**
     * Cinematic radial-wipe transition.
     * Expands a disc from the origin element (the clicked toggle button, if available)
     * to cover the viewport, commits the theme, then fades out the disc.
     * Falls back to CSS transition + optional View Transition API if disc approach is skipped.
     */
    function _animatedApply(valid, effective, persist, originEl) {
      if (reducedMotionQuery.matches) {
        _commitTheme(valid, effective, persist);
        return;
      }

      // View Transition API path (Chromium 111+ / Firefox 131+ with flag)
      if (document.startViewTransition && !transitionInFlight) {
        transitionInFlight = true;

        // Tell CSS which direction we're going so pseudo-elements can handle it
        document.documentElement.setAttribute('data-theme-transitioning', effective);

        // Set clip-path origin based on toggle button position
        if (originEl) {
          const rect = originEl.getBoundingClientRect();
          const cx = Math.round(rect.left + rect.width / 2);
          const cy = Math.round(rect.top  + rect.height / 2);
          document.documentElement.style.setProperty('--dp-vt-x', cx + 'px');
          document.documentElement.style.setProperty('--dp-vt-y', cy + 'px');
        } else {
          // Default: top-right (where toggles typically live)
          document.documentElement.style.setProperty('--dp-vt-x', (window.innerWidth - 48) + 'px');
          document.documentElement.style.setProperty('--dp-vt-y', '32px');
        }

        const transition = document.startViewTransition(() => {
          _commitTheme(valid, effective, persist);
        });

        transition.finished.finally(() => {
          transitionInFlight = false;
          document.documentElement.removeAttribute('data-theme-transitioning');
        });
        return;
      }

      if (transitionInFlight) return;
      transitionInFlight = true;

      // Manual overlay fallback (all other browsers)
      const isDark = (effective === 'dark');
      const overlay = document.createElement('div');
      overlay.className = 'dp-theme-overlay';
      overlay.style.setProperty('--dp-overlay-color', isDark ? '#030712' : '#f8fafc');

      if (originEl) {
        const rect = originEl.getBoundingClientRect();
        const cx = ((rect.left + rect.width  / 2) / window.innerWidth  * 100).toFixed(1) + '%';
        const cy = ((rect.top  + rect.height / 2) / window.innerHeight * 100).toFixed(1) + '%';
        overlay.style.setProperty('--dp-vt-x', cx);
        overlay.style.setProperty('--dp-vt-y', cy);
      }

      document.body.appendChild(overlay);

      // Force reflow then expand disc
      overlay.getBoundingClientRect();

      overlay.classList.add('dp-theme-overlay--revealed');

      // Commit theme at ~60% through the disc expansion
      const EXPAND_MS = 330;
      setTimeout(() => { _commitTheme(valid, effective, persist); }, EXPAND_MS * 0.5);

      // Fade overlay out after full expansion
      setTimeout(() => {
        overlay.classList.add('dp-theme-overlay--fade');
        setTimeout(() => {
          overlay.remove();
          transitionInFlight = false;
        }, 310);
      }, EXPAND_MS + 30);
    }

    function applyTheme(pref, persist = true, originEl = null) {
      const valid     = (pref === 'light' || pref === 'dark' || pref === 'system') ? pref : 'system';
      const effective = resolveTheme(valid);

      // If this is the very first paint / non-interactive call, skip animation
      if (!persist || !originEl) {
        _commitTheme(valid, effective, persist);
        return;
      }

      _animatedApply(valid, effective, persist, originEl);
    }

    // ── Toggle factory ────────────────────────────────────────────────────

    function createToggle() {
      const container = document.createElement('div');
      container.className = 'dp-theme-toggle';
      container.setAttribute('role', 'radiogroup');
      container.setAttribute('aria-label', 'Theme preference');

      const items = [
        {
          mode:  'light',
          label: 'Light',
          title: 'Light theme',
          icon:  `<svg class="dp-theme-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <circle cx="12" cy="12" r="4"/>
                    <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>
                  </svg>`
        },
        {
          mode:  'dark',
          label: 'Dark',
          title: 'Dark theme',
          icon:  `<svg class="dp-theme-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>
                  </svg>`
        },
        {
          mode:  'system',
          label: 'System',
          title: 'System (Follow OS)',
          icon:  `<svg class="dp-theme-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <rect width="20" height="14" x="2" y="3" rx="2"/>
                    <line x1="8" x2="16" y1="21" y2="21"/>
                    <line x1="12" x2="12" y1="17" y2="21"/>
                  </svg>`
        }
      ];

      const current = getSavedPreference();

      items.forEach(item => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'dp-theme-btn';
        btn.setAttribute('role', 'radio');
        btn.setAttribute('data-mode', item.mode);
        const isCurrent = item.mode === current;
        btn.setAttribute('aria-checked', String(isCurrent));
        btn.setAttribute('aria-label', `${item.label} theme`);
        btn.title = item.title;
        btn.tabIndex = isCurrent ? 0 : -1;
        btn.innerHTML = `${item.icon}<span class="label">${item.label}</span>`;

        if (isCurrent) {
          const svg = btn.querySelector('svg');
          if (svg) svg.classList.add('dp-icon-active');
        }

        btn.addEventListener('click', (e) => {
          applyTheme(item.mode, true, e.currentTarget);
        });

        btn.addEventListener('keydown', (e) => {
          let target = null;
          if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
            e.preventDefault();
            target = btn.nextElementSibling || container.firstElementChild;
          } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
            e.preventDefault();
            target = btn.previousElementSibling || container.lastElementChild;
          } else if (e.key === 'Home') {
            e.preventDefault();
            target = container.firstElementChild;
          } else if (e.key === 'End') {
            e.preventDefault();
            target = container.lastElementChild;
          }
          if (target) { target.focus(); target.click(); }
        });

        container.appendChild(btn);
      });

      return container;
    }

    // ── OS media query live sync ──────────────────────────────────────────
    try {
      mediaQuery.addEventListener('change', () => {
        if (getSavedPreference() === 'system') {
          applyTheme('system', false, null);
        }
      });
    } catch (_) {
      if (mediaQuery.addListener) {
        mediaQuery.addListener(() => {
          if (getSavedPreference() === 'system') {
            applyTheme('system', false, null);
          }
        });
      }
    }

    // Apply immediately upon script execution (anti-FOUC reinforcement)
    applyTheme(getSavedPreference(), false, null);

    return {
      init()             { applyTheme(getSavedPreference(), false, null); },
      setTheme(mode, el) { applyTheme(mode, true, el || null); },
      getTheme()         { return getSavedPreference(); },
      getEffectiveTheme(){ return resolveTheme(getSavedPreference()); },
      createToggle
    };
  })();

  // Expose on window
  window.DigiPathTheme = DigiPathTheme;

  // ── 2. Shell Navigation & Profile Pod Setup ───────────────────────────────
  const path     = location.pathname;
  const initials = value =>
    String(value || 'U').trim().split(/\s+/).filter(Boolean)
      .slice(0, 2).map(v => v[0]).join('').toUpperCase();

  function avatar(profile) {
    const el = document.createElement('span');
    el.className = 'dp-avatar';
    if (profile.avatar_url) {
      const img = document.createElement('img');
      img.src = profile.avatar_url;
      img.alt = '';
      el.append(img);
    } else {
      el.textContent = initials(profile.full_name || profile.email);
    }
    return el;
  }

  function addNav() {
    document.querySelectorAll('.nav-list, .nav').forEach(nav => {
      if (nav.querySelector('a[href="/chatbot"]')) return;
      const a = document.createElement('a');
      a.href = '/chatbot';
      a.className = nav.classList.contains('nav-list') ? 'nav-item' : '';
      a.innerHTML = nav.classList.contains('nav-list')
        ? '<span class="nav-icon">✦</span><span>AI_ASSISTANT</span>'
        : 'AI Assistant';
      if (path === '/chatbot') a.classList.add('active');
      nav.append(a);
    });
  }

  function addProfile(profile) {
    const label = (profile.full_name || profile.email || 'PROFILE').split(' ')[0];

    document.querySelectorAll('.nav-list, .nav').forEach(nav => {
      if (nav.querySelector('a[href="/profile"]')) return;
      const a = document.createElement('a');
      a.href = '/profile';
      a.className = nav.classList.contains('nav-list') ? 'nav-item dp-shell-profile-link' : '';
      a.innerHTML = nav.classList.contains('nav-list')
        ? '<span class="nav-icon">◉</span><span>PROFILE_SETTINGS</span>'
        : 'Profile & settings';
      nav.append(a);
    });

    if (!document.getElementById('headerAvatar') && !document.querySelector('.dp-profile-pod')) {
      const anchor = document.createElement('a');
      anchor.href = '/profile';
      anchor.className = 'dp-profile-pod';
      anchor.append(avatar(profile));
      const text = document.createElement('span');
      text.textContent = label.toUpperCase() + ' // PROFILE';
      anchor.append(text);

      const host = document.querySelector(
        '.topbar-actions, .nav-links, .topbar-right, .header, header, nav > div'
      );
      if (host) host.append(anchor);
    }

    document.querySelectorAll('#navInitials').forEach(el => { el.textContent = ''; el.append(avatar(profile)); });
    document.querySelectorAll('#navName').forEach(el => el.textContent = label.toUpperCase());
    document.querySelectorAll('.user-avatar').forEach(el => {
      if (!el.querySelector('img')) { el.textContent = ''; el.append(avatar(profile)); }
    });
  }

  function addThemeToggle() {
    if (!document.querySelector('.dp-theme-toggle')) {
      const toggle = DigiPathTheme.createToggle();
      const host = document.querySelector(
        '.topbar-right, .topbar-actions, .top-nav, .header, header, nav > div, .statusbar, .status-bar'
      );

      if (host) {
        const ref = host.querySelector(
          '#userPillWrap, #loginNavBtn, .dp-profile-pod, .user-profile, .status-tag, #profileLink, .status-bar-links'
        );
        ref ? host.insertBefore(toggle, ref) : host.appendChild(toggle);
      } else {
        const sidebarHost = document.querySelector('.sidebar-header, .sidebar-footer, aside');
        if (sidebarHost) sidebarHost.appendChild(toggle);
      }
    }

    // Mount second toggle in Profile settings page
    const profileMount = document.getElementById('profileThemeSettingMount');
    if (profileMount && !profileMount.querySelector('.dp-theme-toggle')) {
      profileMount.appendChild(DigiPathTheme.createToggle());
    }

    if (!document.getElementById('dp-theme-announcement')) {
      const announcement = document.createElement('p');
      announcement.id = 'dp-theme-announcement';
      announcement.className = 'dp-visually-hidden';
      announcement.setAttribute('aria-live', 'polite');
      announcement.setAttribute('aria-atomic', 'true');
      document.body.appendChild(announcement);
    }
  }

  async function boot() {
    DigiPathTheme.init();
    addNav();
    addThemeToggle();

    try {
      const res = await fetch('/api/user/profile', { credentials: 'include' });
      if (!res.ok) return;
      const profile = await res.json();
      addProfile(profile);
    } catch (_) {
      /* Public pages remain usable without an authenticated profile. */
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
