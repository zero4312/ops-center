/* 临时诊断脚本：渲染 ops-center 页面，检查顶部应用下拉框选项数量与资源清单列。 */
const { chromium } = require('playwright-core');
const fs = require('fs');

const EXEC = '/Users/lvpeng/Library/Caches/ms-playwright/chromium-1208/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';
const BASE = process.env.OC_BASE || 'http://127.0.0.1:8000';

(async () => {
  const browser = await chromium.launch({
    executablePath: fs.existsSync(EXEC) ? EXEC : undefined,
    args: ['--no-sandbox', '--no-proxy-server'],
  });
  const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));

  await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2500);

  // 登录
  await page.fill('input[placeholder="用户名"]', 'admin');
  await page.fill('input[type="password"]', 'Admin@12345');
  await page.click('button:has-text("登 录")');
  await page.waitForTimeout(3500);

  // 打开顶部应用下拉
  await page.click('.oc-app-select');
  await page.waitForTimeout(1200);

  const optTexts = await page.$$eval('.el-select-dropdown__item', els => els.map(e => e.textContent.trim()));
  const visible = await page.$$eval('.el-select-dropdown:visible .el-select-dropdown__item', els => els.length).catch(() => -1);
  const wrapBox = await page.$$eval('.el-select-dropdown', els => els.map(e => {
    const r = e.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height), top: Math.round(r.top), display: getComputedStyle(e).display };
  }));
  const listBox = await page.$$eval('.el-scrollbar__wrap', els => els.slice(0, 3).map(e => {
    const r = e.getBoundingClientRect();
    return { h: Math.round(r.height), sh: e.scrollHeight, ch: e.clientHeight, overflowY: getComputedStyle(e).overflowY };
  }));

  console.log('== 顶部应用下拉 ==');
  console.log('option 总数:', optTexts.length);
  console.log('可见 dropdown 内 item 数:', visible);
  console.log('前 8 项:', JSON.stringify(optTexts.slice(0, 8)));
  console.log('dropdown 盒子:', JSON.stringify(wrapBox));
  console.log('scrollbar wrap:', JSON.stringify(listBox));

  await page.screenshot({ path: '/tmp/oc_app_dropdown.png' });
  await page.keyboard.press('Escape');
  await page.waitForTimeout(500);

  // 资源清单列
  await page.click('text=资源清单');
  await page.waitForTimeout(2500);
  const headers = await page.$$eval('.el-table__header th .cell', els => els.map(e => e.textContent.trim()));
  console.log('\n== 资源清单表头 ==');
  console.log(JSON.stringify(headers));
  await page.screenshot({ path: '/tmp/oc_resources.png', fullPage: false });

  console.log('\n== 控制台错误 ==');
  console.log(errors.slice(0, 10).join('\n') || '(无)');

  await browser.close();
})().catch(e => { console.error('FAILED:', e.message); process.exit(1); });
