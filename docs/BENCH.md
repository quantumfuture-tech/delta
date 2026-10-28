# Benchmark

- machine: Windows-11-10.0.26200-SP0 / Python 3.14.4
- typing (key -> composer paint): p50 2.77 ms, p95 7.76 ms, max 15.09 ms
- streaming token batch (5 chars -> incremental draft append + body paint): p50 5.94 ms, p95 8.56 ms, max 9.14 ms
- layer switch (prefix diff + rebuild suffix): p50 9.50 ms, p95 15.83 ms, max 15.83 ms

Method: one warm-up key sequence, one warm-up streaming append, and one warm-up layer switch precede the measurements; 160 injected key events with an immediate repaint of the composer; 70 streaming batches of 5 Chinese characters appended to an in-flight card with an immediate repaint; 20 alternating layer switches. Real deltas are batched by the app on a 14 ms timer, so the end-to-end p95 is about 14 ms plus the render number above. Layer switches alternate between two already-rendered layers, so the card cache is exercised; the first visit to an unseen layer additionally renders its cards.
