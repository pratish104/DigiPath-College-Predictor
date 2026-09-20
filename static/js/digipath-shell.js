/* Shared, authenticated application shell. It deliberately consumes the existing profile API. */
(function () {
  const routes = [
    ['/dashboard','▣','DASHBOARD'], ['/predictor','◈','PREDICTOR'], ['/institutes','◉','INSTITUTES'],
    ['/job-recommender','▤','JOB_MATCH'], ['/resume-analyzer','▦','RESUME_5D_AI'], ['/resume-builder','⚡','RESUME_BUILDER'],
    ['/roadmap','➹','ROADMAP'], ['/scam-detector','⚠','SCAM_DETECTOR'], ['/chatbot','✦','AI_ASSISTANT']
  ];
  const path = location.pathname;
  const initials = value => String(value || 'U').trim().split(/\s+/).filter(Boolean).slice(0,2).map(v => v[0]).join('').toUpperCase();
  function avatar(profile) { const el=document.createElement('span'); el.className='dp-avatar'; if(profile.avatar_url){ const img=document.createElement('img'); img.src=profile.avatar_url; img.alt=''; el.append(img); } else el.textContent=initials(profile.full_name||profile.email); return el; }
  function addNav() {
    document.querySelectorAll('.nav-list, .nav').forEach(nav => {
      if (nav.querySelector('a[href="/chatbot"]')) return;
      const a=document.createElement('a'); a.href='/chatbot'; a.className=nav.classList.contains('nav-list') ? 'nav-item' : ''; a.innerHTML=nav.classList.contains('nav-list') ? '<span class="nav-icon">✦</span><span>AI_ASSISTANT</span>' : 'AI Assistant';
      if(path==='/chatbot') a.classList.add('active'); nav.append(a);
    });
  }
  function addProfile(profile) {
    const label=(profile.full_name||profile.email||'PROFILE').split(' ')[0];
    document.querySelectorAll('.nav-list, .nav').forEach(nav => {
      if(nav.querySelector('a[href="/profile"]')) return;
      const a=document.createElement('a'); a.href='/profile'; a.className=nav.classList.contains('nav-list')?'nav-item dp-shell-profile-link':'';
      a.innerHTML=nav.classList.contains('nav-list')?'<span class="nav-icon">◉</span><span>PROFILE_SETTINGS</span>':'Profile & settings'; nav.append(a);
    });
    if(!document.getElementById('headerAvatar') && !document.querySelector('.dp-profile-pod')) {
      const anchor=document.createElement('a'); anchor.href='/profile'; anchor.className='dp-profile-pod'; anchor.append(avatar(profile)); const text=document.createElement('span'); text.textContent=label.toUpperCase()+' // PROFILE'; anchor.append(text);
      const host=document.querySelector('.topbar-actions, .nav-links, .header, header, nav > div'); if(host) host.append(anchor);
    }
    document.querySelectorAll('#navInitials').forEach(el=>{ el.textContent=''; el.append(avatar(profile)); });
    document.querySelectorAll('#navName').forEach(el=>el.textContent=label.toUpperCase());
    document.querySelectorAll('.user-avatar').forEach(el=>{ if(!el.querySelector('img')) { el.textContent=''; el.append(avatar(profile)); } });
  }
  async function boot() {
    addNav();
    try { const res=await fetch('/api/user/profile',{credentials:'include'}); if(!res.ok) return; const profile=await res.json(); addProfile(profile); }
    catch (_) { /* Public pages remain usable without an authenticated profile. */ }
  }
  document.addEventListener('DOMContentLoaded',boot);
}());
