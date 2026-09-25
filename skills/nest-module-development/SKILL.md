---
name: nest-module-development
description: Use this skill whenever the agent or a developer needs to create, extend, debug, package, install, or uninstall a custom module for the Nest (小窝) private home framework. Trigger on requests to add a new page/feature/entry to 小窝, add a sidebar button, store custom data, build a tool-only module, write module.json, write page.js, install a module from a link, or fix a module whose page or sidebar entry does not appear. Covers the capability declaration contract, the lightweight (webui) runtime, the python runtime, module storage, and the three-layer gating rules.
---

# Nest Module Development

小窝 is a framework. Diary, impressions, media and memos are just official modules on top of it. This skill is the stable path for adding a new one.

Everything here describes contracts that exist in code as of `0.6.0`. Do not invent fields.

## Decide the runtime first

There are two tiers. Pick the lightweight one unless you have a concrete reason not to.

`runtime: "webui"` is the recommended path and the default. The module is two files — `module.json` and `page.js` — plus optional assets. It renders inside 小窝's own shell and persists data through the framework's module store. No Python, no server, no dependency install. A bot can produce a working module of this tier on its own.

`runtime: "python"` is the escape hatch, for modules that genuinely need server-side work. It runs inside the plugin process, so 小窝 will never auto-enable it after installation — the user must switch it on deliberately. Reach for this only when the lightweight tier cannot express the feature.

## Capability declaration

A module declares which surfaces it participates in. Anything it does not declare, it does not get. This is deliberate: a tool-only module simply omits `nav` and `page` and therefore has no sidebar entry and no view.

```json
{
  "id": "habit-board",
  "name": "习惯打卡",
  "type": "module",
  "version": "1.0.0",
  "description": "记录每天的打卡，并统计连续天数。",
  "runtime": "webui",
  "nav": { "label": "打卡", "icon": "modules", "order": 310 },
  "page": { "entry": "page.js", "export": "mount", "title": "习惯打卡" },
  "store": true,
  "feature_tags": ["habit"],
  "schema_version": 2
}
```

`nav` controls the sidebar entry. `label` is the text, `icon` is either a built-in icon name (`home`, `diary`, `search`, `impressions`, `media`, `memos`, `modules`, `settings`, `appearance`, `access`, `backup`, `webui`) or a relative path to an svg/png the module ships. `order` places the entry: official entries occupy 10–60, settings sits at 900, and custom modules default to 500. Omit `nav` entirely for a module with no UI.

`page` points at the ES module the framework will import. `entry` must be a relative `.js` or `.mjs` path inside the module directory. `export` names the mount function and defaults to `mount`.

`store` turns on the framework's per-module JSON storage. Set it to `true`, or to an object with `max_bytes` if you want a smaller cap than the 1 MB default.

Declaring `nav` without `page` is rejected — an entry that leads nowhere is worse than no entry. The framework reports this on the module card instead of silently dropping it.

## The page contract

`page.js` exports a mount function. 小窝 hands it an empty panel and a capability bundle, and expects an optional handle back.

```js
export async function mount(root, ctx) {
  const items = (await ctx.store.get("items", [])) || [];
  root.innerHTML = `<header class="topbar">…</header><section class="card">…</section>`;
  root.querySelector("[data-add]")?.addEventListener("click", async () => {
    await ctx.store.set("items", [...items, Date.now()]);
    ctx.notify("已记录");
  });
  return {
    async update() { /* 重新进入这一页时调用 */ },
    unmount() { root.innerHTML = ""; },
  };
}
```

`ctx` provides everything a module needs so it never touches auth, bridge transport, or theming itself:

`ctx.store` has `keys()`, `get(key, fallback)`, `set(key, value)`, `remove(key)`. Keys are limited to letters, digits, underscore, dot and hyphen, at most 64 characters, at most 64 keys per module, and 1 MB per document. Data lands in `modules/<module-id>/data/store/<key>.json`.

`ctx.request(path, options)` calls the module's own namespaced backend at `/api/ui/modules/<module-id>/…`. Only meaningful for `runtime: "python"`.

`ctx.assetUrl(relativePath)` resolves a file the module ships. `ctx.notify(message)` shows a toast, `ctx.reportError(message)` shows an error, `ctx.confirm(message)` opens 小窝's own confirm dialog — never call `window.confirm`, it does not work inside the AstrBot plugin page. `ctx.escapeHtml` and `ctx.icon(name, label)` are the framework's own helpers. `ctx.refresh()` reloads bootstrap and re-renders. `ctx.insidePluginPage` tells you whether the page is running in the AstrBot plugin page rather than a browser tab.

Return `unmount` if you attach anything outside `root` — timers, document listeners, observers. The framework calls it when the module is disabled, uninstalled, or its entry file changes.

## Styling

Use 小窝's shared variables and component classes so the three official appearances theme the module for free: `--paper`, `--panel`, `--wash`, `--ink`, `--muted`, `--line`, and classes like `.card`, `.card-head`, `.card-body`, `.topbar`, `.page-title`, `.button`, `.button.primary`, `.chip`, `.chips`, `.muted`, `.notice`, `.setting-line`, `.form-grid`.

Do not write four parallel stylesheets for default, 纸庭, 玻璃小屋 and 夜间工作室. Add a narrow override only when a component has a genuinely unique visual surface.

## Where files live

A module installed from a link, or created by hand, lives in one of:

```text
modules/<module-id>/                    # 完整模块，含数据
modules/extensions/<extension-id>/      # 拓展包
framework/user_custom/webui/appearance/<id>/   # 外观包
```

Frontend files may sit at the module root or under a `webui/` subdirectory; both are searched. Persistent data always belongs in `modules/<module-id>/data/`, never hidden inside a frontend folder.

Only these asset types are served: `.js .mjs .css .json .svg .png .jpg .jpeg .webp .gif .woff2 .html .txt .md`. Path traversal is rejected.

## Restart after direct changes

When you create or modify a module by directly writing files, you must tell the user to restart AstrBot (or reload the plugin) before checking the new sidebar entry or page. A browser refresh alone does not reliably reload the plugin runtime and its module discovery state. This reminder is part of the completion response, not an optional troubleshooting note.

For modules installed through the module console, follow the install result first. If an entry is still missing, refresh the page after the install completes, then use the three-layer checks below.

## Three-layer gating

When a page or sidebar entry does not appear, walk these in order — the cause is almost always one of them.

The module must declare the capability. No `nav`, no entry; no `page`, no view.

The user must enable the module in 模块控制台. Assets and the store return 404 for a module that is installed but switched off. A user can also keep a module enabled while hiding just its sidebar entry.

The framework must verify the declaration. If `page.entry` or the nav icon does not resolve to a real file of an allowed type, the entry is withheld and the reason is shown on the module card. Check there first.

## Install and uninstall

Installing from a link accepts a zip or GitHub repository containing `module.json`. Official IDs (`diary`, `impressions`, `media`, `memos`, `webui`, and the official appearance IDs) are refused. Existing directories are backed up to `imports/module-install-backups/` before being overwritten. A `runtime: "python"` package is never auto-enabled.

Uninstall backs the module directory up to `imports/module-uninstall-backups/` and then removes it. 卸载并保留数据 keeps `modules/<id>/data` in place and only removes the frontend and the switches. Official modules cannot be uninstalled.

## Do not

Do not edit official module code or bundled WebUI files to customize behavior for one user — `modules/diary/`, `nest_diary_web/diary/`, `nest_diary_web/web_dist/`, `skills/nest-diary/`. If a change should become the default, open a focused PR instead.

Do not replace an official module in place. Create `diary-plus` with `replaces` and `conflicts_with` set, and let the module console warn the user.

Do not ship controls with nothing behind them. Every visible button, switch and form must map to a real store key, route or setting. Hide what is not finished.

Do not use `localStorage` for module data — use `ctx.store`, so exports, backups and imports actually include it.

## Verify before reporting done

Confirm the sidebar entry appears with the expected label, icon and position; the page mounts without console errors; data survives a reload; the module still behaves when switched off and back on; and, if you have access, that it works both in a browser tab and in the AstrBot plugin page.

Report which files you created under `modules/<module-id>/`, which capabilities the module declares, and what you actually exercised. A visual mockup that cannot run is not a finished module.
