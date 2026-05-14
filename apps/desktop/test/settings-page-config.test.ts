import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

function run(name: string, fn: () => void) {
  fn();
  console.log(`ok - ${name}`);
}

const projectRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const settingsPageSource = readFileSync(join(projectRoot, "src/pages/SettingsPage.tsx"), "utf8");
const settingsConfigSource = readFileSync(join(projectRoot, "src/pages/settingsConfig.tsx"), "utf8");

function uniqueMatches(source: string, pattern: RegExp): string[] {
  return Array.from(new Set(Array.from(source.matchAll(pattern), (match) => match[1]).filter(Boolean)));
}

const categoryIds = uniqueMatches(settingsConfigSource, /\{\s*id:\s*"([^"]+)"/g);
const categorySet = new Set(categoryIds);

run("settings page uses only configured category ids", () => {
  assert.ok(categoryIds.length > 0, "expected settings categories to be discoverable");

  const referencedCategories = [
    ...uniqueMatches(settingsPageSource, /category:\s*"([^"]+)"/g),
    ...uniqueMatches(settingsPageSource, /activeCategory === "([^"]+)"/g),
    ...uniqueMatches(settingsPageSource, /setActiveCategory\("([^"]+)"\)/g),
  ];

  for (const category of referencedCategories) {
    assert.ok(categorySet.has(category), `unknown settings category: ${category}`);
  }
});

run("settings search items target rendered focus anchors", () => {
  const searchTargets = uniqueMatches(settingsPageSource, /targetKey:\s*"([^"]+)"/g);
  const focusTargets = new Set(uniqueMatches(settingsPageSource, /registerFocusTarget\("([^"]+)"\)/g));

  assert.ok(searchTargets.length > 0, "expected settings search targets to be discoverable");

  for (const target of searchTargets) {
    assert.ok(focusTargets.has(target), `missing focus target for search/config key: ${target}`);
  }
});

run("legacy settings category ids were fully migrated", () => {
  const legacyIds = ["general", "directories", "fileManagement", "model", "llm", "summary", "advanced", "environment"];

  for (const legacyId of legacyIds) {
    assert.equal(categorySet.has(legacyId), false, `legacy category still configured: ${legacyId}`);
    assert.equal(settingsPageSource.includes(`category: "${legacyId}"`), false, `legacy category still referenced: ${legacyId}`);
    assert.equal(settingsPageSource.includes(`activeCategory === "${legacyId}"`), false, `legacy active category still rendered: ${legacyId}`);
    assert.equal(settingsPageSource.includes(`setActiveCategory("${legacyId}")`), false, `legacy category still navigated: ${legacyId}`);
  }
});

run("video settings can trigger bilibili cookie capture", () => {
  assert.ok(settingsPageSource.includes("captureBilibiliLoginCookies"), "missing settings cookie capture handler");
  assert.ok(settingsPageSource.includes("window.desktop?.bilibili"), "settings cookie capture should reuse desktop bilibili bridge");
  assert.ok(settingsPageSource.includes("createBilibiliCookieQrcode"), "settings cookie capture should support web qrcode fallback");
  assert.ok(settingsPageSource.includes("pollBilibiliCookieQrcode"), "settings cookie capture should poll web qrcode login");
  assert.ok(settingsPageSource.includes("登录获取"), "missing visible cookie capture button");
});
