# Benchmark

- machine: Windows-11-10.0.26200-SP0 / Python 3.14.4
- typing (key -> composer paint): p50 1.07 ms, p95 2.43 ms, max 3.80 ms
- streaming token batch (5 chars -> incremental draft append + body paint): p50 6.27 ms, p95 9.57 ms, max 14.87 ms
- branch switch (prefix diff + rebuild suffix): p50 14.90 ms, p95 19.68 ms, max 19.68 ms

Method: one warm-up key sequence and one warm-up streaming append precede the measurements; 160 injected key events with an immediate repaint of the composer; 70 streaming batches of 5 Chinese characters appended to an in-flight card with an immediate repaint; 20 alternating branch switches. Real deltas are batched by the app on a 14 ms timer, so end-to-end token latency is bounded by one timer tick plus the render number above. With a 14 ms tick and the streaming p95 of 9.57 ms, the end-to-end p95 is about 23.6 ms, comfortably under two 16.7 ms frames. Branch switching alternates between two already-rendered branches, so the cache is exercised; the first visit to an unseen branch additionally renders its cards.