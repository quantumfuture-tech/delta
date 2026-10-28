# Benchmark

- machine: Windows-11-10.0.26200-SP0 / Python 3.14.4
- typing (key -> composer paint): p50 1.16 ms, p95 2.94 ms, max 7.64 ms
- streaming token batch (5 chars -> incremental draft append + body paint): p50 6.98 ms, p95 11.14 ms, max 12.13 ms
- layer switch (prefix diff + rebuild suffix): p50 7.53 ms, p95 25.83 ms, max 25.83 ms

Method: one warm-up key sequence and one warm-up streaming append precede the measurements; 160 injected key events with an immediate repaint of the composer; 70 streaming batches of 5 Chinese characters appended to an in-flight card with an immediate repaint; 20 alternating layer switches. Real deltas are batched by the app on a 14 ms timer, so the end-to-end p95 is about 14 + 11.14 = 25.1 ms, under two 16.7 ms frames. Layer switches alternate between two already-rendered layers, so the card cache is exercised; the first visit to an unseen layer additionally renders its cards.
