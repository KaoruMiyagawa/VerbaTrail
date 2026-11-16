# Listening Assessment B2/B3 acceptance index

B2's [quality gates](../listening-initial-assessment-b2/quality-gates.json) record 187 backend and 54 frontend tests, typecheck, production build, and the B2 browser run. The 4 tests recorded below are a later focused run, not a repeat of the full suites.

The earlier [B3 summary](summary.json) captured an incomplete checkpoint. Its pending areas were subsequently tested in the separate reports linked below; it is retained as the original checkpoint.

- [Lost response: browser](browser-loss.json), [server and database](backend-evidence.json): a real successful submit response was dropped and the existing result recovered.
- [V1 historical Reading and Listening 409](v1-browser.json), [V1 learner-model evidence](v1-model-evidence.json).
- [Run switching and delayed responses](switch/summary.json), with [browser events](switch/browser.json) and [server records](switch/backend.json).
- [Reading and Listening isolation](isolation/summary.json), with [browser events](isolation/browser.json) and [server records](isolation/backend.json).
- [Touch and audio failure](touch/summary.json), with [touch events](touch/touch-report.json) and [server records](touch/backend.json).

These reports refer to isolated local development users and temporary SQLite databases. Selected screenshots illustrate each stage; additional local screenshots and failed-attempt files are intentionally retained outside the commit. The touch report notes a visible Premium focus indicator at the bottom edge of the viewport and that physical speaker output was not assessed. The tests use the project's learner ID development convention, not authentication.
