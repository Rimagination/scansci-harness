"use strict";

const { isIP } = require("node:net");

function browserUrl(input) {
  if (typeof input !== "string" || !input.trim() || /[\u0000-\u001f\\]/.test(input)) {
    throw new Error("请输入有效的网页地址");
  }
  const address = input.trim();
  let parsed;
  try {
    parsed = new URL(/^[a-z][a-z\d+.-]*:/i.test(address) ? address : `https://${address}`);
  } catch {
    throw new Error("网页地址无效");
  }
  const host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
  if (!["http:", "https:"].includes(parsed.protocol) || !host || parsed.username || parsed.password) {
    throw new Error("只支持不含账号密码的 HTTP(S) 网页地址");
  }
  if (host === "localhost" || host.endsWith(".localhost") || isIP(host)) {
    throw new Error("内置浏览器只接受域名，不能打开 IP 地址或本地服务");
  }
  return parsed.href;
}

function isAppUrl(input, origin) {
  try {
    const url = new URL(input);
    const trusted = new URL(origin);
    return url.protocol === trusted.protocol && url.hostname === trusted.hostname
      && url.port === trusted.port && !url.username && !url.password;
  } catch {
    return false;
  }
}

module.exports = { browserUrl, isAppUrl };
