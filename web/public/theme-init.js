// Paint the right background before the bundle loads - no white strobe on
// dark-mode reloads. Loaded synchronously (no defer) in index.html; kept as
// an external file so strict-CSP deployments can allow it by URL.
if (window.matchMedia('(prefers-color-scheme: dark)').matches
    && localStorage.getItem('omnicloud-theme') !== 'light') {
  document.documentElement.style.background = '#09090B';
}
