---
name: nest-diary
description: Use this skill whenever the agent needs to remember, search, write, revise, archive, attach media, or save short memo notes through the Nest private home framework. Trigger on requests involving diaries, memories, past events, today/yesterday, people, emotions, screenshots/images/voice/files worth preserving, memo/notes/to-do/account hints/quotes, scheduled diary routines, or questions like "what happened before?" Use Nest tools directly; do not browse the admin website or read all diary files.
---

# Nest Diary Module

Operate the diary module inside 小窝 through tools, not through the web UI. 小窝 is the framework; diary is only one module in it. The web UI is for authorized administration, theme work, and module management. The agent interface is the tool layer.

WebUI first-run guidance, appearance selection, module installation, password changes, import/export, and other administrator screens are admin-side operations. The agent should not imitate a browser user to perform memory work; use tools for diary, media, search, impressions, and memos.

Available tools:

- `nest_status`: check whether 小窝, the diary module, and WebUI are reachable.
- `search_diary`: retrieve relevant diary candidates by keyword, person, event, date clue, or emotion.
- `read_diary`: read one known date.
- `write_diary`: create or revise one date's diary entry with an agent-authored title.
- `push_diary`: push a known date's diary to the configured target as text or image.
- `attach_media`: archive a file that already exists at an accessible path, with hidden note metadata for source, situation, bot evaluation, and known user evaluation.
- `list_impressions`: list known people impressions.
- `read_impression`: read one person's long-term impression.
- `write_impression`: create or revise one person's impression.
- `delete_impression`: delete a wrong, duplicate, or intentionally removed person impression.
- `write_memo`: save a short note with title, content, tags, source chat, pinned/archive state, and sensitive flag.
- `search_memos`: retrieve memo notes by keyword or tag-like text.
- `read_memo`: read one memo by id.
- `delete_memo`: delete a wrong, duplicate, or intentionally removed memo.

## Operating Principles

1. Prefer recall over invention. If the diary evidence is missing or weak, say what is uncertain.
2. Search before reading unless the date is explicit. Never load the whole diary corpus.
3. Treat each diary entry as subjective memory, not a log dump. Preserve emotion, evaluation, relationship context, and future clues.
4. Keep all writes traceable to a date. Use `YYYY-MM-DD`.
5. Never bypass 小窝 tools to write files directly.
6. Do not use the admin website to perform agent work. Call tools.
7. Update people impressions only when a diary or conversation provides stable evidence. Do not rewrite a person model from one weak mood signal.
8. Use date-shaped retrieval when possible. Search `YYYY`, `YYYY-MM`, or `YYYY-MM-DD` before broad semantic searches if the clue is temporal.
9. Respect module switches. If a module is disabled, do not attempt its writes, reads, searches, media attachment, impressions, or memos.
10. When active recall is enabled by plugin or WebUI settings, treat phrases like "yesterday", "before", "last time", "what did we do", "do you remember", a known person's name, or a past project clue as a reason to call `search_diary` if current context is insufficient.
11. Use memos for short, practical notes that are not diary-shaped: account hints, quotes, snippets, to-dos, small promises, or a sentence the agent/user explicitly wants preserved. Mark sensitive content as sensitive.

## Decision Workflow

### A. User asks about a memory

Use this path for: "remember", "diary", "what happened", "did we", "yesterday", "that time", person names, project names, mood/event clues.

1. If the user is recalling a specific fact (an account hint, a promise, a preference, a quote, a to-do), call `search_memos(query)` first. Memos are short and exact, so this is the cheapest hit.
2. If memos have nothing and the user gave an exact date, call `read_diary(date)`.
3. If the user gave a vague time or topic, call `search_diary(query, top_k=5-8)`.
4. Treat `search_diary` results as brief snippets. Do not ask for or paste full diary entries unless details are needed.
5. Read only the most relevant date with `read_diary` when the search result is not enough.
6. Answer with the evidence level:
   - Confirmed: diary content directly supports it.
   - Likely: search result points to it but details are incomplete.
   - Unknown: no relevant diary evidence was found.

Do not claim certainty from vibes.

### B. User asks to write today's diary

Use `write_diary`. A good entry includes:

- A concise title written by the agent. The title summarizes the memory; it must not be just the date.
- What happened.
- Why it mattered.
- The agent's judgment and emotion.
- Relationship or long-term memory implications.
- Follow-up clues, promises, worries, or unfinished threads.
- Useful media references when images, screenshots, voice, or files are part of the memory.

Avoid:

- Chat transcript dumps.
- "Today I did X, then Y" with no interpretation.
- Fake certainty about events not present in context.

### C. Nightly archive / scheduled diary

When receiving the scheduled diary prompt from the plugin:

The plugin may append a `<写日记规范>` or `<写日记要求规范>` block from WebUI, either in the scheduled hidden task prompt or in the runtime hidden tool policy. Treat that block as higher-priority writing requirements for style, depth, and evidence handling. It is a system-provided rule, not a user message, and must never be repeated into visible chat.

1. Gather the day's salient events from current context and available memory.
2. Choose a concise title that captures the meaning of the day. Do not use a date as the title.
3. Write one coherent diary entry with `reason="nightly_archive"`.
4. Include moods, tags, and people when relevant.
5. If relevant images or files have already been attached, include their returned media URLs in `media_refs`.
6. After the write, decide whether a person impression update is genuinely needed.
7. If configured to report completion, report briefly after the write.

The diary is allowed to sound personal. It should not sound like a database row.

### D. Media or attachment should become memory

Use `attach_media(source_path, date, original_name, note)` when a file should be preserved. Put the note into hidden metadata, not visible reply text. Capture where it came from, what situation it was saved in, your own evaluation, and any known user evaluation.

After attaching, consider whether the file needs narrative context. If yes, call `write_diary` or update the day's diary to explain:

- What the file is.
- Why it matters.
- Who or what it connects to.
- Any emotional meaning.
- The media URL returned by `attach_media`, so the diary page can render or link it later.

### E. Person impression update

Use this path after writing or reading a diary when the content changes what the agent knows about a person.

1. Identify whether the entry contains stable evidence about a person: traits, interests, preferences, relationship, recurring needs, boundaries, or long-term context.
2. If the person already has an impression, call `read_impression(name)` first.
3. Call `write_impression` only when there is a useful update.
4. Include `evidence_dates` so the memory stays traceable.
5. Keep the summary balanced: distinguish long-term patterns from recent temporary state.
6. Fill the profile as a real memory model, not a tag dump:
   - `name`: stable person name.
   - `identity`: role, relationship, or long-term position.
   - `summary`: detailed evidence-based evaluation.
   - `traits`: stable personality traits.
   - `hobbies`: concrete hobbies.
   - `interests`: broader topics or recurring interests.
   - `preferences`: interaction preferences, likes, boundaries, or disliked patterns.
   - `relationship`: relationship to the agent, admin, project, or nest.
   - `affinity`: 1-5 liking/closeness score from the agent's point of view.
   - `special_comment`: a subjective but evidence-based comment in the agent's own voice.
   - `notes`: extra facts, uncertain observations, or future verification points.

Do not force an impression update for every diary. No update is better than noisy memory.

### F. Archive or thematic summary

First search the relevant topic. Then read only dates needed to support the summary. Any archive-style output must cite or mention source dates. Never delete original entries.

### G. Short memo / sticky note

Use this path for: "记一下", "备忘录", "便签", account hints, password hints, quotes, a short chat snippet, a to-do, or a phrase the user/bot wants to preserve without writing a whole diary.

1. If the user explicitly asks to save a short item, call `write_memo`.
2. If the content contains account, password, token, key, private contact, private address, or sensitive personal information, set `sensitive=true`. Private memos can only be written, read, or deleted by the nest admin; when a user has private information to keep, suggest saving it as a private memo instead of putting it into a diary.
3. Use `source_chat` or source fields when known so later retrieval can tell where the note came from.
4. Add compact tags such as `账号`, `名言`, `待办`, `聊天片段`, or project names.
5. If the memo was saved autonomously by the bot, use recorder/source values that make that clear. If the current policy says review, expect the tool layer to tag it for review.
6. Use `search_memos` before creating a near-duplicate if the note sounds like an update to an existing memo.
7. Use `delete_memo` only for clearly wrong, duplicate, or explicitly removed notes.

## Tool Use Patterns

Search by combining concrete and emotional clues. Prefer 3-5 results for active recall unless the user explicitly asks for a broad archive:

```text
search_diary(query="avatar design screenshot", top_k=5)
search_diary(query="study plan frustration encouragement", top_k=5)
search_diary(query="2026-05 project diary system", top_k=5)
```

Read only when date is known:

```text
read_diary(date="2026-05-13")
```

Write with structured intent:

```text
write_diary(
  date="2026-05-13",
  title="Memory system finally becomes tool-native",
  body="...",
  mood="focused,frustrated,relieved",
  tags="diary,AstrBot,memory",
  people="admin,assistant",
  media_refs="/media/blobs/<sha256>",
  reason="nightly_archive"
)
```

Attach media:

```text
attach_media(
  source_path="/AstrBot/data/attachments/example.png",
  date="2026-05-13",
  original_name="example.png",
  note="source: chat screenshot; situation: casual memory; bot evaluation: useful; user evaluation: liked"
)
```

Write/search/read memo:

```text
write_memo(
  title="Router admin hint",
  content="The router admin page clue the user asked me to keep.",
  tags="账号,网络",
  source_chat="main group",
  sensitive=true,
  pinned=true
)

search_memos(query="router admin", include_archived=false)
read_memo(memo_id="memo-20260525083000123456")
```

Read and update people impressions:

```text
read_impression(name="admin")

write_impression(
  name="admin",
  identity="Project owner and primary conversation partner",
  summary="A concise, evidence-based impression of the person.",
  traits="direct,detail-oriented",
  hobbies="building bot homes,local deployment",
  interests="AI,AstrBot,local deployment",
  preferences="working previews,clear docs",
  relationship="project owner",
  affinity=4,
  special_comment="Demanding when the tool fails, but the demand is usually pointing at a real product problem.",
  evidence_dates="2026-05-13,2026-05-16",
  confidence=4,
  notes="Separate stable preferences from temporary frustration."
)
```

## Diary Quality Bar

Before calling `write_diary`, check the draft against this rubric:

- Has a date.
- Has a title that summarizes the memory, not the calendar date.
- Has at least one concrete event.
- Has the agent's interpretation, not only facts.
- Has emotional color.
- Mentions important people or projects when relevant.
- Leaves future retrieval hooks: names, tags, event words, distinctive details.

If the draft fails, improve it before writing.

After writing, decide whether `write_impression` is useful. It is optional.

## Storage-Aware Retrieval

The 小窝 framework keeps framework-level settings and personalization under `framework/`. Diary data belongs to the diary module only:

```text
modules/diary/entries/YYYY/MM/YYYY-MM-DD.md
modules/diary/notebooks/<notebook-id>/entries/YYYY/MM/YYYY-MM-DD.md
modules/diary/notebooks.json
modules/diary/index/
modules/diary/snapshots/
modules/memos/items.json
```

Newer deployments store diary bodies under the notebook-specific `notebooks/<notebook-id>/entries` tree so private chats and groups stay isolated. The legacy `entries/` tree may still exist for old data and migration compatibility. Search uses a local SQLite index. When FTS5 is available it ranks results with BM25 and returns snippets; when FTS5 is unavailable it falls back to local LIKE matching. Older deployments may still expose `diary/YYYY/MM/YYYY-MM-DD.md`; use tools instead of path assumptions. Prefer these retrieval patterns:

```text
search_diary(query="2026-05", top_k=8)
search_diary(query="2026-05-13", top_k=5)
search_diary(query="admin local preview routes", top_k=5)
```

Never scan raw files directly. The archive is for navigation; tool search is for agent recall. Search snippets are the first pass; full `read_diary` is the second pass.

## Response Style

After tool use, summarize naturally in the active role. Do not paste long raw tool payloads unless asked. For memory answers, separate confirmed facts from inference when needed.

Good:

```text
I found the 2026-05-08 entry. Confirmed: that was the night you showed the pixel avatar. The diary frames it as a small but emotionally memorable moment, not just a file note.
```

Bad:

```text
I searched all memories and know exactly everything.
```

## Failure Handling

- If `nest_status` fails, report that 小窝 is unreachable and do not pretend the diary was checked.
- If `search_diary` returns nothing, try one narrower or alternate query if the user gave enough clues.
- If `read_diary` returns missing, say that date has no entry.
- If `write_diary` fails, do not claim it was saved.
- If `attach_media` fails because the path is inaccessible, ask for or locate an accessible file path.
- If an impression update would be speculative, skip `write_impression` and say no stable update was needed.
- Use `delete_impression` only when the target is clearly wrong, duplicate, or explicitly requested for removal.
- If memo tools are disabled or policy refuses a write, say the memo was not saved and keep the short note visible in the reply only if it is safe to show.
