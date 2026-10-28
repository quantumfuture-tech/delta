# Test results

Command:

```powershell
& D:\LMY\coding\aichat\_py\Scripts\python.exe -m pytest -q tests
```

Result (2026-10-28, Windows 11, Python 3.14.4, PySide6 6.11.1):

```
57 passed in 12.93s
```

Raw output: [pytest-output.txt](pytest-output.txt)

## What is covered

| Area | Tests | Guarantee |
|---|---|---|
| Tree semantics | `test_model.py` | one root; parent/child; sibling branches; pointer = position; chain = root→pointer; preferred child change touches one node; deletion only on explicit request; retry creates a sibling; stream append is monotonic |
| Context | `test_context.py` | only the visible chain enters the prompt; reasoning is never sent back; empty placeholders skipped |
| Persistence | `test_store.py` | atomic save/load of every field (pointer, preferred, text, meta, UI); crash mid-stream becomes `interrupted` with text intact; trash on delete; no temp files; corrupt file is reported |
| Streaming | `test_client.py` | SSE content + reasoning; 401/403, 429 + Retry-After, 5xx; timeout; dropped connection; cancellation after partial; malformed events; `[DONE]`-less completion; missing key |
| Incremental UI | `test_ui.py` | a branch switch preserves the exact widget objects in the common prefix; off-screen cards are reused on return; streaming cards refresh when returned; no full list rebuild |
| Restart | `test_ui.py` | pointer, content and scroll anchor survive close/reopen; same top row and offset within 2 px |
| Rapid operations | `test_ui.py` | second send while streaming ignored; switching branch mid-stream; stop twice; partial text stays on its own node; a new send works afterwards |
| Streaming text | `test_markdown.py` | fast draft strips syntax markers but keeps content |
| Input | `test_ui.py` | Enter sends; Alt+↑/↓ cycles sibling branches; the final markdown re-applies the full stylesheet after streaming |
| Fork lifecycle | `test_ui.py` | retry completion / interruption / 401 update or remove the placeholder chip without a click; the chip width is recomputed |
| Destructive UI | `test_ui.py` | accepting the delete dialog removes the node and decrements its parent; rejecting it changes nothing |
| Role actions | `test_ui.py` | a user card exposes exactly 3 actions and never a retry button; an assistant card exposes 4 |
| Fork rows | `test_ui.py` | 5 sibling chips keep two rows after initial render, a chip switch, and an upstream jump; every chip stays inside the bar height |

## Acceptance mapping

1. **Real model, streaming, interrupt, no content loss** — live probe: `status=complete`, 4 SSE tokens, 1.05s; UI captures show `LIVE` and `INTERRUPTED · 已保留 147 字`. Interrupt test asserts the partial text is in the node and in the saved JSON.
2. **Key→char / token→screen** — [BENCH.md](BENCH.md): typing p95 2.43 ms; streaming render p95 9.57 ms; with the 14 ms UI batch, worst typical token path ≈ 23.6 ms (< 2 frames at 60 Hz).
3. **Reflow limited to affected part** — `test_branch_switch_only_replaces_suffix` asserts prefix widget identity; `test_switch_back_reuses_cached_cards` asserts the returned card object is the same one.
4. **Restart identical** — `test_restart_restores_pointer_and_scroll`; JSON is atomic; scroll is stored as `(row key, offset)`.
5. **Rapid operations** — `test_rapid_operations_do_not_crosstalk` plus the double-send and double-stop assertions.

## Screenshots

- [main.png](screenshots/main.png) — demo conversation, two forks, ATLAS; the lower fork is a completed retry
- [fork_after_retry.png](screenshots/fork_after_retry.png) — the fork bar after retry completion, no click: both chips show real answer text
- [fork_two_rows.png](screenshots/fork_two_rows.png) — 5 sibling chips in two rows after switching/upstream; all 5 visible
- [streaming.png](screenshots/streaming.png) — live token stream, stop affordance
- [interrupted.png](screenshots/interrupted.png) — partial answer kept after stop
- [error401.png](screenshots/error401.png) — classified auth error with recovery action
- [markdown.png](screenshots/markdown.png) — code card, quote, table, headings
- [empty.png](screenshots/empty.png) — first-run surface
- [library.png](screenshots/library.png) — conversation library
- [v1_conventional.png](screenshots/v1_conventional.png) — rejected first version