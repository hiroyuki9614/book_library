# Reader repair investigation — 2026-10-27

Status: in progress; production acceptance is not yet complete.
Issue: #46. Baseline: PR #43 / merge `49e8bc8`.

## Confirmed causes

- RTL EPUB next/previous buttons inverted logical next/previous. On the actual
  demo publication at its beginning, next repeatedly called prev and remained at
  the same CFI, body range and 0 / 230 location. This is a reproduced stopped
  location, not merely integer percentage rounding.
- PDF restoration depended on a document scope recreated on each render and a
  ref shared with persistence. i5 production UI reproduced 3→4→3→4 on next clicks.
- The actual demo EPUB has one formal nav.xhtml link, one content spine item,
  no NCX, and real body chapter headings 一 through 十一. Supplementary navigation
  must use those real headings. Other publications' nested TOC entries were
  dropped by top-level-only rendering.
- The demo's horizontal source inherited epub.js's injected HTML direction:rtl
  from OPF page progression. Paragraph direction and page progression were
  conflated. Body-only vertical writing also needed detection before axis sizing.
- Edge taps were removed in PR #43. Save failures were suppressed, writes could
  arrive out of order, and immediate reentry could fetch a stale position.
- Stronger numerical assertions exposed epub.js vertical spread divisor/count
  disagreement and a one-page overestimate of the visible end, affecting bounds.
  Spreads now compose two actual single-page renditions in publication order,
  including mixed horizontal/vertical spine transitions.
- Native word-based mapping can return the preceding page's CFI for Japanese
  text nodes without spaces. Source-preserving visible-glyph Range mapping uses
  rectangle pruning and bisection, with native fallback for image-only pages.
  Vertical CFI restoration snaps on viewport height rather than horizontal width.

## Source and comparison

The production demo EPUB and the reproduced official-source conversion have
SHA-256 `751e469c8b180ce19c7da9bf99d366be7087bda178f0a55e27fde481158ea19b`.
Original bytes and R2 objects were not replaced. Calibre 7.6 opened the identical
EPUB and rendered its horizontal Japanese text, punctuation and ruby. Comparison
captures are kept with operator evidence. Test fonts must support Japanese
vertical glyph metrics; Noto Serif CJK JP matches the i5 environment.

## Previous inspection gap

The old EPUB E2E directly PATCHed a fixed CFI and never proved visible UI page
movement. Mocked reader tests proved only calls. PDF E2E checked the input number,
not the visible image, and retained an obsolete button-name selector after PR #43.
PR #43's production record used short mobile single-page checks, without i5,
continuous spread-mode movement, or per-operation body/CFI/location correlation.
The new acceptance runbook requires these observations and separate restore/error
and adjacent-page checks.

## i5 identification and baseline

Established SSH configuration identifies ubuntu-worker as the i5. Verified CPU:
Intel Core i5-7300U; OS: Ubuntu 26.04.1 LTS; actual GUI Google Chrome
154.0.8037.57; Xorg GUI display 1080×2280. The browser runs on that physical host,
controlled through its loopback CDP endpoint over the existing SSH route.
Baseline reproduction used normal production login/shelf and UI buttons.
No renderer next/prev or React state was called directly for acceptance.

Final commit, CI, independent-review disposition, immutable deployment identity,
post-deploy GUI results and evidence index will be appended after verification.
