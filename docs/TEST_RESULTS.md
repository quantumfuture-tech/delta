# Test results

Command:

    & D:\LMY\coding\aichat\_py\Scripts\python.exe -m pytest -q

Result (2026-10-30, Windows 11, Python 3.14.4, PySide6 6.12.0):

    138 passed in 44.43s

Raw output: [pytest-output.txt](pytest-output.txt)

## What is covered

| Area | Tests | Guarantee |
|---|---|---|
| View is derived | test_model.py | view = ancestors(descend(baseline)); changing a default or the baseline changes the view; there is no stored path field |
| Locks are derived | test_model.py | locked layers = layers crossed by root -> baseline; baseline at root => empty lock set; setting the baseline up releases a layer |
| Write scope | test_model.py | a direct layer write outside commands raises ScopeError; a malicious command that writes another layer raises ScopeError; every basic command declares its write layer |
| Inverses | test_model.py | AddChild -> CloseChild; SetActive/Reorder -> reverse; CloseChild/RepairEmpty/root -> irreversible; composite inverse is declared |
| Four layer doors | test_model.py | activate / create / close / reorder all raise LayerLocked on the shortcut chain; open layers succeed |
| Close | test_model.py | closing the active child activates the next display neighbour, else the previous, else clears; birth order never changes |
| Converge | test_model.py | the global command writes only the shortcut layers' defaults, keeps the baseline and the view; the one global exception |
| Defensive delete | test_model.py | removing a subtree that contains the baseline moves it to the nearest surviving ancestor (not reachable through a layer entrance) |
| Display order | test_model.py | appending, reordering and closing keep birth order; display order is a permutation; the view does not move |
| Slots / drafts | test_model.py | per-slot drafts; dirty/reset; mode switch confirm + one-step undo; clean switch seamless; default and legacy slot mapping; persistence; prune; clean drafts are omitted from ui.slots while still following the initial text |
| Slot commands | test_model.py | new question is a 2-step composite with one inverse; new answer is single; revise question keeps the original; rewrite with/without feedback; self-edit is non-model; root writes the root then the answer; declared read ids; can_send rules |
| Draft actions | test_model.py | reset, mode switch and propose-question-messages write no node |
| Migration | test_migration.py | schema 1 -> 2: the old visible chain becomes the default route; baseline = root; no locks; mid-branch pointer truncates; schema 2 roundtrip |
| Context | test_context.py | only the view enters the prompt; reasoning is never sent back; empty placeholders skipped |
| Persistence | test_store.py | atomic save/load of nodes, defaults, baseline, display order, meta, UI slots; crash mid-stream becomes interrupted; trash; schema 2 on disk |
| Streaming | test_client.py | SSE content + reasoning; 401/403, 429 + Retry-After, 5xx; timeout; dropped connection; cancellation after partial; malformed events; missing key |
| UI: layer strip | test_ui.py | every layer has the same 72px row (title line + tab row); horizontal scroll on overflow; wheel/Shift+wheel scroll horizontally and are consumed at both ends; each tab has a visible + that opens its slot; open tab and tail chip have an open state; single-child layers keep the labelled row with +/×; add opens a slot (no node); close/drag are commands |
| UI: slot line | test_ui.py | the target marker shares the tab's number/title/outline; the write-layer label is explicit; off-view turns the marker red and shows locate; locked says the baseline moves first; reset enabled only when dirty |
| UI: clean drafts | test_ui.py | opening a slot with no text leaves ui.slots empty; typing creates an item; reset removes it again |
| UI: modes | test_ui.py | answer base switches model/self mode; dirty confirm; one-step undo; per-slot drafts survive switching |
| UI: send | test_ui.py | send executes the active slot; new question/answer; rewrite empty = regenerate; rewrite feedback reads the base; self-edit makes no model call; active slot moves to the new view leaf tail |
| UI: locks | test_ui.py | clicking a locked tab does not switch; confirming raises the baseline; sending on a locked write layer writes nothing and keeps the draft; after unlocking, send again works |
| UI: tab drag | test_ui.py | a real gesture (press, move past 6px, drop) hides the chip, opens the gap, posts one reorder and leaves the view alone; Esc puts the tab back and writes nothing |
| UI: proposal | test_ui.py | propose question replaces the draft only; no node; draft dirty |
| UI: ATLAS encodings | test_atlas.py | edge roles split at the baseline (root -> baseline is the shortcut, below it the route, siblings are plain); one default edge per layer; node tags for baseline / current / shortcut / branch |
| UI: ATLAS click | test_ui.py | clicking a node changes the baseline and nothing else: node count, per-layer defaults and display order stay; the lock set is derived from the shortcut chain |
| UI: incremental | test_ui.py | a switch preserves the exact widget objects in the common prefix; off-screen cards are reused on return |
| UI: target | test_ui.py | the marker number equals the tab number (02), the title is the tab title, and the same tab is open |
| UI: restart | test_ui.py | active slot and draft survive close/reopen |
| UI: rapid | test_ui.py | second send while streaming ignored; switching layers mid-stream; double stop; partial text stays on its own node |
| UI: no bypass | test_ui.py | a static scan of delta/ui asserts no direct layer write, no raw scope call, no direct convo.create/activate/close/reorder/send/ask/answer/abandon, no old API names |
| Streaming text | test_markdown.py | fast draft strips syntax markers but keeps content |
| UI: turn rail | test_turnrail.py | one mark per turn, prompt grouped with its answer; the active mark is the last turn at the bottom and the turn crossing the reading line elsewhere; a click scrolls that turn to 24px below the top and leaves the baseline alone; the frame caps at 420px and scrolls inside itself, a short rail ignores the wheel; fewer than two turns draws nothing; the preview card opens left of the rail, centred on its mark, and carries the turn number plus clipped prompt/response |

## Acceptance mapping

1. **Real model, streaming, interrupt, no content loss** — previous live probe: status=complete, 4 SSE tokens, 0.84 s; the UI stream path is covered with a fake client (complete, interrupted, 401), and partial text is asserted in both the node and the saved JSON.
2. **Key -> char / token -> screen** — [BENCH.md](BENCH.md): typing p95 7.29 ms; streaming render p95 9.92 ms; the UI batches deltas every 14 ms, so the end-to-end p95 is about 23.9 ms, under two 16.7 ms frames. Layer switch p95 31.54 ms after a warm-up (measured before the tab-strip round; still under two frames).
3. **Reflow limited to the affected part** — test_activate_replaces_only_the_suffix asserts prefix identity; test_switch_back_reuses_cached_cards asserts the returned card is the same object.
4. **Restart identical** — test_restart_restores_active_slot_and_draft; JSON is atomic; the scroll anchor is (row key, offset); the active slot/draft are versioned in ui.slots.
5. **Rapid operations** — test_rapid_operations_do_not_crosstalk plus double-send/double-stop assertions.

## Not covered / known

- Still open in the visual layer: the scrollbar look, and re-shooting the screenshots (they predate the flush tab).
- The interrupt test no longer races the wall clock: the fake client emits a short prefix and then parks until the job is cancelled, so "interrupted" cannot flip to "complete" on a busy machine (it did, twice, before this change).
- Single-child layers keep their own 72px labelled row, not a connected tab; every layer adds a row, so long conversations scroll longer.
- The current screenshots show behavior, not final visual design.
- All UI writes go through commands; the one declared repair is RepairEmpty (a streaming placeholder that never received text), also a command with a write layer and a gate.
