# Test results

Command:

    & D:\LMY\coding\aichat\_py\Scripts\python.exe -m pytest -q

Result (2026-10-28, Windows 11, Python 3.14.4, PySide6 6.11.1):

    90 passed in 15.08s

Raw output: [pytest-output.txt](pytest-output.txt)

## What is covered

| Area | Tests | Guarantee |
|---|---|---|
| View is derived | test_model.py | view = ancestors(descend(baseline)); changing a default or the baseline changes the view; there is no stored path field |
| Locks are derived | test_model.py | locked layers = layers crossed by root -> baseline; baseline at root => empty lock set; setting the baseline up releases a layer |
| Four doors | test_model.py | activate / create / close / reorder all raise LayerLocked on the shortcut chain; open layers succeed; no direct public delete |
| Close | test_model.py | closing the active child activates the next display neighbour, else the previous, else clears the layer; birth order never changes |
| Converge | test_model.py | the global command writes only the shortcut layers' defaults, keeps the baseline and the view, and is the only write to a locked layer |
| Defensive delete | test_model.py | removing a subtree that contains the baseline moves it to the nearest surviving ancestor (not reachable through a layer entrance) |
| Display order | test_model.py | appending, reordering and closing keep birth order; display order is a permutation; the view does not move |
| Migration | test_migration.py | schema 1 -> 2: the old visible chain becomes the default route; baseline = root; no locks; mid-branch pointer truncates; schema 2 roundtrip |
| Context | test_context.py | only the view enters the prompt; reasoning is never sent back; empty placeholders skipped |
| Persistence | test_store.py | atomic save/load of nodes, defaults, baseline, display order, meta, UI; crash mid-stream becomes interrupted; trash; schema 2 on disk |
| Streaming | test_client.py | SSE content + reasoning; 401/403, 429 + Retry-After, 5xx; timeout; dropped connection; cancellation after partial; malformed events; missing key |
| UI: layer strip | test_ui.py | one row, fixed height, horizontal scroll on overflow; chips show titles and birth numbers; add/close/drag wired to the same layer actions |
| UI: locks | test_ui.py | clicking a locked tab does not switch; it opens the inline confirmation; confirming raises the baseline and all four actions work afterwards |
| UI: ATLAS | test_ui.py | a click changes only the baseline; defaults/texts unchanged; reset releases every lock |
| UI: incremental | test_ui.py | a switch preserves the exact widget objects in the common prefix; off-screen cards are reused on return |
| UI: restart | test_ui.py | baseline, content and scroll anchor survive close/reopen within 2 px |
| UI: rapid | test_ui.py | second send while streaming ignored; switching layers mid-stream; double stop; partial text stays on its own node; a new send works afterwards |
| UI: no bypass | test_ui.py | a static scan of delta/ui asserts no assignment to default_child / baseline_id / display_order / nodes, and no call to the internal remove/touch helpers |
| Streaming text | test_markdown.py | fast draft strips syntax markers but keeps content |

## Acceptance mapping

1. **Real model, streaming, interrupt, no content loss** — live probe on 2026-10-28: status=complete, 4 SSE tokens, 0.84 s, text "分叉很好。"; the UI stream path is covered with a fake client (complete, interrupted, 401), and the partial text is asserted in both the node and the saved JSON.
2. **Key -> char / token -> screen** — [BENCH.md](BENCH.md): typing p95 2.94 ms; streaming render p95 11.14 ms; the UI batches deltas every 14 ms, so the end-to-end p95 is about 25.1 ms, under two 16.7 ms frames. Layer switch p95 25.83 ms (still under two frames; it rebuilds one strip and the suffix).
3. **Reflow limited to the affected part** — test_activate_replaces_only_the_suffix asserts prefix identity; test_switch_back_reuses_cached_cards asserts the returned card is the same object.
4. **Restart identical** — test_restart_restores_baseline_and_scroll; JSON is atomic; the scroll anchor is (row key, offset).
5. **Rapid operations** — test_rapid_operations_do_not_crosstalk plus the double-send / double-stop assertions.

## Not covered this round

- The visual layer (lock affordance, connected single-child tab, scrollbar look) is next round; the single-child layer is reachable through the card context menu for now.
- Screenshots under docs/screenshots come from the previous round; the two screenshots that showed the removed two-row strip / pin dot were deleted with their scripts.
