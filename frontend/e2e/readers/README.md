Owner LTR direction regression (2026-11-09)
=========================================

Run from the repository root with the existing locked dependencies:

```sh
npm --prefix frontend run test -- src/features/components/EpubReader src/features/components/readerNavigation.test.ts
npm --prefix frontend run test:readers -- direction.spec.ts
npm --prefix frontend run test:readers
```

`directionEpubFixture` in `fixtures.ts` builds a synthetic horizontal EPUB in
memory: four chapters, eight explicitly separated columns each, and numbered
source text. OPF progression, publisher `html { direction:… !important }`, and
body bidi can oppose the book setting passed through the harness. No personal
book, database, or staging seed is used.

`direction.spec.ts` uses real Chromium/epub.js layout. At desktop, 393px and
320px, it checks single/always/auto, ten next and three previous turns with
buttons/iframe keys/edge taps, numeric CFI ordering, exact visible source columns,
spread geometry, chapter seams, first/last boundaries, and server-CFI reload
restore with local storage cleared. It also checks stage/root/body direction and
the root's inline priority after layout and content hooks. Text blocks keep
publisher bidi, including RTL text in an LTR publication. The existing navigation
suite covers vertical writing, mixed chapters, selection, reflow, and real
PDF/PDFium rendering. Its ten-next/three-previous mode checks return to Chapter 1
through the TOC before each mode, so accumulated movement cannot exhaust the
finite vertical fixture in the final mode.

Each case writes `direction-observations.json` and spread screenshots in its
Playwright output directory under `frontend/test-results/`. The desktop LTR case
with opposing OPF/body also writes `owner-ltr-direction.epub` there, regenerated
from the fixture source. All outputs are ignored artifacts; do not commit EPUB,
PDF, or test-result binaries.

When staging is admitted, the owner can import this regenerated book with the
book setting `pageTurnDirection=ltr`. Its OPF/body intentionally remain RTL.

The defect was observable as SOURCE PAGE 08 at Chapter 1/page 1, with CFI
`epubcfi(/6/2!/4/16/1:0)`: the stage followed LTR while root/body columns followed
RTL. epub.js retains OPF direction in Layout after Rendition.direction() updates
its manager, and its column formatter only assigns root direction for RTL.
Publisher body direction independently controls column progression. The view
now aligns horizontal column containers and formatting with the manager before
measurement, applying root direction as inline `!important` after epub.js column
formatting. Source bidi capture preserves both the source and injected inline
priorities, as well as text nodes. Repeated,
unchanged layout notifications also preserve a restored second-page CFI instead
of saving the spread's earlier page over it.

These are isolated headless harness checks. Physical i5 GUI acceptance and an
admitted staging dataset remain the staging owner's work, following
`docs/runbooks/reader-acceptance.md`. The generated book is ready for that later
import; running these tests does not admit, seed, or deploy staging.
