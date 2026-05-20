(function(){
  const KEY = 'hclv_theme';
  const btn = document.getElementById('theme-toggle');
  function applyTheme(t){
    if(t === 'dark') document.documentElement.setAttribute('data-theme','dark');
    else document.documentElement.removeAttribute('data-theme');
    if(btn) btn.textContent = t === 'dark' ? '☀️' : '🌙';
  }
  const saved = localStorage.getItem(KEY) || (document.cookie.match(/theme=(dark)/)?.[1]);
  if(saved) applyTheme(saved);
  window.toggleTheme = function(){
    const cur = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    const next = cur === 'dark' ? 'light' : 'dark';
    applyTheme(next === 'dark' ? 'dark' : '');
    localStorage.setItem(KEY, next === 'dark' ? 'dark' : '');
    document.cookie = `theme=${next === 'dark' ? 'dark' : ''}; path=/`;
  };
  if(btn) btn.addEventListener('click', toggleTheme);
})();