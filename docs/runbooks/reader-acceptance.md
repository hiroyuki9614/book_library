# Reader acceptance

Use this checklist for a reader release candidate and again after production
deployment. Do not use mobile emulation as the i5 GUI-browser result.

## Candidate identity

- Record the exact commit SHA and preview URL.
- Verify the preview serves images built from that SHA.
- Use isolated synthetic or public-domain books; do not expose personal books
  or credentials in evidence.

## Automated gates

From `frontend/` run:

```sh
npm test
npm run test:node
npm run lint
npm run build
npm run test:readers
```

Retain the existing authenticated reader E2E and release CI. The real-rendering
suite must use epub.js and PDFium rather than component mocks.

## i5 GUI-browser gate

Record the host/CPU, OS, GUI browser/version, URL, SHA, time, orientation/window
size, and console/network errors. In both EPUB and PDF verify:

1. the initial position is one-based and does not briefly show invalid zeroes;
2. LTR right-edge and RTL left-edge taps advance, while opposite edges go back;
3. buttons, links, selection, drag/scroll, zoom, and the open TOC do not trigger
   unintended turns;
4. hierarchical TOC/outline jumps work, missing PDF outlines show the explicit
   no-TOC message, and EPUB can return to its pre-TOC position;
5. progress follows buttons, edge taps, direct page input, scrolling, and TOC;
6. reload restores the saved CFI/page and does not overwrite it with the initial
   rendered position;
7. single/spread changes and portrait/landscape resize preserve a usable
   location; the final position retains completed behavior.

Run the same checklist against production after deployment. Report Android
separately as verified or unverified.
