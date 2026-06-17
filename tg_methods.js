(async () => {
  const im = window.appImManager;
  const methods = Object.getOwnPropertyNames(Object.getPrototypeOf(im)).filter(m => typeof im[m] === 'function' && /open|username|peer/i.test(m));
  return methods;
})()
