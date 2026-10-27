# Reader navigation acceptance

Run `npm --prefix frontend run test:readers` for production-built EPUB/PDF
rendering regression. Fixtures are generated from ZIP/OPF/nav/spine/CSS and
Chromium-created PDFs. Only the protected file/progress HTTP boundary is replaced
by the isolated test server; epub.js, PDFium, EmbedPDF, DOM layout, and UI actions
are real. Japanese vertical tests need Noto CJK fonts.

## Required observations

A call to next/prev, a changed input, a successful PATCH, and HTTP 200 are
insufficient. After each UI action, match the visible body or page image with its
position. EPUB observations include current CFI, chapter screen page, character
location, PATCH payload/result, subsequent GET, and restored visible body.

EPUB has two position scales:

- **章内ページ**: screen pages within the current spine section, recalculated for
  the viewport and layout. This changes on ordinary page turns.
- **本文位置**: one-based index in epub.js locations generated approximately every
  1600 characters. It is not the number of screen pages. Integer percentage and
  character-location numbers can remain unchanged for a small move.

The saved EPUB position is CFI, not a screen-page number. A successful server GET
is authoritative; local CFI is only a fallback when that GET fails, with a warning.
Per-book PATCHes are serialized in one browser tab and a remount's GET waits for
pending saves. Save failures remain visible with a retry for the current position.
This does not implement multi-device conflict resolution.

Spreads contain two real single-page renditions: primary and following, arranged
right-to-left for RTL publications and left-to-right for LTR. This also applies
across mixed writing-mode chapters. Verify actual pane geometry, source text
continuity, and settled layout at odd viewport widths (393px). A same-spine turn
must retain its existing preview document; an increased total or two copies of the same page is a failure.
The reading viewport's `aria-busy` remains true until queued UI movement settles.

## Device acceptance

Record device identity, CPU, OS, browser/version, actual GUI display/session,
UTC/JST time, screen size, and reading viewport. Identify the i5 from established
configuration, not a guessed host. A server browser or device-name emulation is
not i5 acceptance. Narrow desktop viewports are not Android device testing.

Open the production URL, log in normally, and enter from the shelf. Use visible
UI clicks, pointer actions and keyboard input. DOM/CFI/network observation is
allowed; do not invoke reader methods or update state with JavaScript.

| Area | Required acceptance |
| --- | --- |
| EPUB modes | Single, spread, auto; desktop, 393px and 320px reading areas |
| EPUB movement | 10 next + 3 previous in each mode; visible range and CFI change per action |
| EPUB input | Buttons, iframe keys, left/right edges; rtl/ltr meanings and arrows agree |
| EPUB interaction | Link activation and text selection remain usable; repeat selection deep inside a long chapter, including Shift+Arrow expansion; visible text and CFI must remain unchanged after native scroll events; repeated input is ordered |
| Boundaries | Within chapter, across chapter/spine, start and end; no false stop elsewhere |
| TOC | All nested entries; distant destinations show the intended text |
| Sparse TOC | Preserve formal TOC; label real body headings as supplemental; invent no headings |
| Typography | Compare actual source CSS and another reader; preserve vertical writing, ruby and punctuation |
| Page seams | Adjacent visible ranges follow original text without missing or repeated characters |
| EPUB restore | Saved CFI/body after reload, shelf reentry, logout/login; repeat without local position cache |
| PDF | 5 next + 2 previous, distant number entry, scrolling, zoom, first/middle/last |
| PDF restore | Visible page image and number agree after reload and logout/login |
| Save failure | Visible error and retry; delayed writes cannot overwrite newer queued positions |

Capture safe screenshots or short video and machine-readable per-action results.
Use only public-domain demo/synthetic content in public artifacts. Exclude
credentials, cookies, signed URLs, object keys and personal-book content. Retain
private details only in the existing operator evidence directory.

## Release gate

Run focused tests, frontend tests/node tests/lint/build, backend checks, applicable
CI, and independent review of the final candidate. Preserve immutable previous
images and the production Compose override for rollback. Deploy through the
established operator path, then repeat the production i5 GUI acceptance above.
If i5 access or any mandatory observation fails, report **i5実機確認未完了** or the
specific failure and keep acceptance incomplete. Never substitute another
browser's result for this gate.

For an optional same-file local diagnosis, set `READER_ACTUAL_EPUB_PATH` to an
absolute protected local EPUB path and run the narrow odd-width rendering case.
The default CI fixture is generated; no uploaded EPUB/PDF is committed. This
local diagnosis is separate from the mandatory production physical-i5 gate.
