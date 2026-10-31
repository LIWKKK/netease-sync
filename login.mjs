#!/usr/bin/env node
/**
 * 无头浏览器扫码登录网易云，把 Cookie 写到 state/cookies.json。
 *
 *   node login.mjs                 # 启动无头 Chromium，导出二维码，等 180 秒
 *   node login.mjs --wait 300      # 多等一会儿
 *   node login.mjs --headed        # 有桌面环境时可看真窗口
 *
 * 二维码会持续刷新到 state/login-qr.png，手机网易云 App「扫一扫」确认即可。
 * 登录成功后自动关闭浏览器，同步脚本直接读这份 Cookie。
 */
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATE = path.join(HERE, 'state');
const CHROME = process.env.CHROME_PATH || '/usr/bin/chromium';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
         + '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';

const argv = process.argv.slice(2);
const headed = argv.includes('--headed');
const waitArg = argv.indexOf('--wait');
const WAIT = waitArg >= 0 ? Number(argv[waitArg + 1]) || 180 : 180;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function log(...a) { console.log(...a); }

async function saveCookies(ctx) {
  const cookies = await ctx.cookies();          // MUSIC_U 挂在 .163.com 上，不能按 URL 过滤
  const pick = Object.fromEntries(cookies.map((c) => [c.name, c.value]));
  const page = await ctx.newPage();
  let profile = {};
  try {
    const resp = await page.request.get('https://music.163.com/api/nuser/account/get', {
      headers: { Referer: 'https://music.163.com/', 'User-Agent': UA },
    });
    profile = (await resp.json())?.profile || {};
  } catch (e) {
    log('校验账号失败:', e.message);
  }
  await page.close();
  if (!profile.userId) return null;
  fs.mkdirSync(STATE, { recursive: true });
  const out = path.join(STATE, 'cookies.json');
  fs.writeFileSync(out, JSON.stringify({
    cookies: cookies.map((c) => ({ name: c.name, value: c.value })),
    uid: profile.userId,
    nickname: profile.nickname,
    saved: Math.floor(Date.now() / 1000),
  }, null, 1), { mode: 0o600 });
  fs.chmodSync(out, 0o600);
  return { out, profile, pick };
}

import { execFileSync } from 'node:child_process';
import { PNG } from 'pngjs';
import jsQR from 'jsqr';

/** 解码校验：只有真的解出一串 codekey 网址才算拿到了可扫的码。 */
function decodes(file) {
  try {
    const png = PNG.sync.read(fs.readFileSync(file));
    const hit = jsQR(new Uint8ClampedArray(png.data), png.width, png.height);
    return Boolean(hit && /codekey=/.test(hit.data));
  } catch {
    return false;
  }
}

/** 二维码截图只有 150px 左右，放大 4 倍方便手机扫。 */
function upscale(file) {
  const big = file.replace(/\.png$/, '.big.png');
  try {
    execFileSync('/usr/bin/ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-i', file,
      '-vf', 'scale=iw*4:ih*4:flags=neighbor', big], { stdio: 'ignore' });
    fs.renameSync(big, file);
  } catch { /* 放大失败就用原图 */ }
}

/** 同步一份到工作区根部的易读文件名，方便直接点开。 */
function mirror(file) {
  try { fs.copyFileSync(file, path.join(HERE, '扫码登录.png')); } catch {}
}

async function grabQr(page, outPath) {
  // 网易云的二维码在 iframe 里，先翻遍所有 frame 找尺寸合适的 canvas/img
  const frames = page.frames();
  for (const f of frames) {
    for (const sel of ['canvas', 'img.qrcode', 'img[src^="data:image"]', '.qrcode img', 'img']) {
      const el = await f.$(sel).catch(() => null);
      if (!el) continue;
      const box = await el.boundingBox().catch(() => null);
      if (!box || box.width < 80 || box.width > 400) continue;
      const probe = outPath + '.probe.png';
      await el.screenshot({ path: probe }).catch(() => null);
      if (fs.existsSync(probe) && decodes(probe)) {
        fs.renameSync(probe, outPath);          // 只把能解的码落盘
        upscale(outPath);
        mirror(outPath);
        return true;
      }
      if (fs.existsSync(probe)) fs.unlinkSync(probe);
    }
  }
  return false;                       // 解不出来就什么都不写，保留上一张好码
}

const browser = await chromium.launch({
  executablePath: CHROME,
  headless: !headed,
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--lang=zh-CN'],
});

const ctx = await browser.newContext({
  userAgent: UA,
  viewport: { width: 460, height: 760 },
  locale: 'zh-CN',
  timezoneId: 'Asia/Shanghai',
});
const page = await ctx.newPage();

log('打开网易云登录页…');
await page.goto('https://music.163.com/#/login', { waitUntil: 'domcontentloaded', timeout: 45000 });
await sleep(2500);

// 切到扫码登录
for (const label of ['扫码登录', '二维码登录']) {
  const tab = page.locator(`text=${label}`).first();
  if (await tab.count()) {
    await tab.click({ timeout: 3000 }).catch(() => {});
    await sleep(1500);
    break;
  }
}
await page.goto('https://music.163.com/login', { waitUntil: 'domcontentloaded' }).catch(() => {});
await sleep(2500);

fs.mkdirSync(STATE, { recursive: true });
const qrPath = path.join(STATE, 'login-qr.png');
const ok = await grabQr(page, qrPath);
log(`二维码已保存: ${qrPath}${ok ? '' : '（整页截图，请人工确认）'}`);
log('用网易云音乐 App 扫一扫 → 确认登录。等待中…');

const deadline = Date.now() + WAIT * 1000;
let lastRefresh = Date.now();
let done = null;
while (Date.now() < deadline) {
  const cookies = await ctx.cookies();
  if (cookies.some((c) => c.name === 'MUSIC_U')) {
    // 部分流程扫完还要在页面上点一次「确认登录」
    for (const label of ['确认登录', '确认', '继续登录']) {
      const btn = page.locator(`text=${label}`).first();
      if (await btn.count()) { await btn.click({ timeout: 2000 }).catch(() => {}); break; }
    }
    const saved = await saveCookies(ctx);
    if (saved) { done = saved; break; }
  }
  // 只在页面明确显示二维码过期时换新码；平时绝不重截，避免把好码覆盖成残图
  if (Date.now() - lastRefresh > 15000) {
    lastRefresh = Date.now();
    const expired = await page.locator('text=/二维码已失效|二维码已过期|点击刷新/').count().catch(() => 0);
    if (expired) {
      log('二维码已过期，换新码…');
      for (const label of ['点击刷新', '刷新']) {
        const btn = page.locator(`text=${label}`).first();
        if (await btn.count()) { await btn.click({ timeout: 2000 }).catch(() => {}); break; }
      }
      await sleep(2500);
      if (await grabQr(page, qrPath)) log('新二维码已写入 ' + qrPath + '（扫这个）');
    }
  }
  await sleep(2000);
}

await browser.close();

if (done) {
  log(`登录成功：${done.profile.nickname} (uid=${done.profile.userId})`);
  log(`Cookie 已保存: ${done.out}`);
  process.exit(0);
}
log('等待超时或未完成确认，重跑 node login.mjs');
process.exit(1);