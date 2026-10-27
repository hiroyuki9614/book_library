# Reader release repair record — 2026-10-27

## Trigger

The merged PR #45 candidate passed its original automated suite and isolated
preview checks, but i5 Chrome against the real public-domain vertical EPUB found
that a page-2 CFI reopened at the end of the book. The attempted production
deployment was immediately rolled back to the prior images. No database
migration was involved.

## Repair scope

- epub.js initial writing-mode/layout and stable rendition lifecycle
- initial relocation persistence guard and ordered ReadingInfo requests
- resize/TOC navigation serialization without mobile header reflow
- PDFium ArrayBuffer loading, document-scoped commands, and LayoutReady restore
- selection-safe delayed edge taps
- real EPUB/PDF renderer Playwright coverage

## Evidence required before release

- exact candidate SHA and isolated preview URL
- frontend/unit/node/lint/build/real-renderer gates
- existing authenticated reader E2E and release CI
- independent review with no unresolved P0–P2 findings
- i5 GUI Chrome preview acceptance
- post-deployment i5 GUI Chrome production acceptance

Android remains a separately reported result. It is not a replacement for the
i5 acceptance gate.
