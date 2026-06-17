(async () => {
  return Object.keys(window).filter(k => /api/i.test(k) && typeof window[k] === 'object').map(k => k);
})()
