# Frontend usability implementation plan

The user approved the preceding review's seven improvements and typography adjustment.
Keep Vue 3, Element Plus, existing routes and API contracts. Implement inline in the current checkout.

- [x] Preserve dialog drafts in component memory, guard modified closes and page exits, reset after explicit discard/success or context changes. Test reopening, context isolation and successful submission.
- [x] Add typed URL state for track/report search, track filters, board pagination/scenario/tabs. Preserve unrelated query parameters and browser history navigation. Remember parent list URLs and restore scroll after data renders. Test query round trips and invalid values.
- [x] Add GPU/model/status/mine filters based on tracks and owned submissions; accessible labels, match count and reset action. Promote leader values without implying cross-track comparability.
- [x] Localize login, submission/track statuses and common result labels; preserve raw technical keys in details/tooltips. Verify both locales and existing registration behavior.
- [x] Make legacy names links, enlarge InfoHint hit targets with explicit click/focus support, add status semantics to async loading.
- [x] Reuse bounded, internally scrolling dialog layout for Catalog. Adjust prose typography while keeping dense technical metadata compact.
- [x] Run frontend tests, production build and development UI check. Inspect final diff for regressions and document any browser verification limitation.

Verification: 94 frontend tests passed; production typecheck/build passed; all five development page module checks and API proxy health passed against http://127.0.0.1:5183. The existing 5173 server belongs to another checkout and was left running. Vite reports a 565.63 kB charts chunk warning. Browser screenshot testing was unavailable because in-app local navigation returned ERR_BLOCKED_BY_CLIENT.

Read-only review caught and resolved query-navigation scroll jumps, untranslated mobile pending statuses, and the historical revenue-tab fallback. Regression coverage includes close cancellation, explicit discard, successful submission, unconfirmed outcomes, context isolation, URL/history state, and scroll restoration after data loading.
