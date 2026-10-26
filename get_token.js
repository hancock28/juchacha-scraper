// 剧查查 / ADX：登录后在 adxray.dataeye.com 页面 Console 粘贴执行
// 输出可直接写进 config.json 的 JSON
(() => {
  const read = (re) => (document.cookie.match(re) || ['', ''])[1];
  const out = {
    userKey: (window.App && App.userKey) || localStorage.getItem('userKey') || read(/userKey=([^;]+)/) || '',
    cookie: document.cookie,
  };
  console.log(JSON.stringify(out, null, 2));
  return out;
})();
// 若 userKey 为空：Network 面板点开任一 /playlet/* 请求 -> Payload -> 复制 token
