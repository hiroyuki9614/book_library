# BeLib frontend release candidate — 2026-10-31

Status: **candidate only; not merged, packaged for release, or deployed**.

## Identity and scope

- Repository: `hiroyuki9614/book_library`.
- Base: `main` at `f8d69c7f1f9a6676ff268ef3da7d815232a779d2`.
- Candidate branch: `codex/belib-frontend-release-20261031`.
- Scope: frontend reader surface/layout, browser and visible brand labels, and reader E2E diagnostics. No backend, authentication implementation, DB schema/migration, authorization policy, storage object, R2, or deployment configuration changes.
- The owner reports that the base was deployed frontend-only at 18:42:59 JST with image `sha256:49bf745ed0f5b7fd3d8951d64cbeed51d33034c762eed93547be8081c71f63c9`. The runtime container, image ID, served assets, and authenticated production UI were not read back for this candidate.
- Read-only public HTTPS baseline probe at about 22:22 JST: `/health` returned 200 with `{"status":"ok","service":"belib-api"}`. `/login` returned 200 HTML with title `frontend`, icon `/vite.svg`, CSS `/assets/index-BiYwVzS6.css` (69,005 bytes, SHA-256 `8723d0bf368acfecb04659f4dd3eb5bd4871d45f78d5cf2e00364a6074e1e5f3`) and JavaScript `/assets/index-DP9fkZQF.js` (1,241,367 bytes, SHA-256 `3881e955038943fc3c5e5a1797722aab757d410a9e260dc8bf0bd9ae4b899f9b`). This is the reported baseline, not the candidate and not a source/image provenance check.

## Design comparison and implementation

The historical `feat/mobile-reader-design-20261028` reference (`6c4c6d0`) kept its reader paper shell and used a mobile-only 16px body inset with a zero desktop inset. It did not implement the subsequently selected white-and-navy, full-width Paper & Navy surface. The supplied design archive and original `.pen` file were not present in the accessible workspace, so no additional visual details were inferred beyond the confirmed requirements.

The candidate updates the actual `ReaderPage -> EpubReader` route: white header, body, viewer and progress areas; no rounded/shadowed outer paper shell; full-width reading area; 16px left/right EPUB body inset at every viewport; and the existing book title and menus retained. The iframe body width is left to epub.js so its own measurement and horizontal/vertical RTL layout remain intact. A fixed 32px epub.js gap supplies the two 16px inline body paddings. `BeLib` is used for the browser title, login title, side navigation and mobile shelf header.

## Reader timing and behavior evidence

Local Playwright Chromium only; the tests mount the real `EpubReader` component in the reader harness and stub only the file/progress API boundary. They do not cover the authenticated route, production network, or physical device. The source was a generated copy of the public Aozora demo EPUB (about 350k Japanese characters; SHA-256 `751e469c8b180ce19c7da9bf99d366be7087bda178f0a55e27fde481158ea19b`, not added to Git). Existing trace suppression and per-stage 30-second readiness limits were preserved.

In the final diagnostic run, 320px `常に見開き` mode was selected through the UI:

- 22:13:57.488 JST: UI click started.
- 22:13:58.794 JST: reader operation queue changed to 1; renderer became busy at 22:13:58.796.
- 22:14:07.194 JST: Playwright click action returned.
- 22:14:08.237 JST: renderer became idle; queue returned to 0 and `aria-busy` cleared at 22:14:08.250.
- 22:14:08.447 JST: layout, ready checks, and visible-text check completed.

The UI action returned about 8.4 seconds after queue start; renderer activity ended about 9.4 seconds after queue start. The full single/spread/auto matrix completed at both 393px and 320px against the same EPUB. This candidate run did not reproduce a 100-second stall. It cannot determine whether the earlier physical-i5 timeout came from the device renderer or the remote GUI/tool wait, because no approved physical-i5 GUI session was available. Therefore the historical cause and i5 acceptance remain **unverified**.

There is a related, earlier physical-i5 reproduction in [PR #55](https://github.com/hiroyuki9614/book_library/pull/55): a 320px always-spread change on the same long, single-spine EPUB stayed busy for more than 30 seconds. That investigation measured repeated primary/following iframe reconstruction by native `manager.resize()` and did not observe an infinite queue. The reflowable-view retention repair from that PR is present in this candidate's base `PublicationManager.resize()`. It is a plausible prior failure mechanism, but it does not prove that the newer >100-second report had the same cause; this run did not test a physical i5 or the remote-control wait path.

Geometry assertions cover a full-width viewer, 16px left/right EPUB body padding, white body background, and non-overlapping/non-clipped header controls. Existing movement/CFI checks cover 10 next and 3 previous moves in each applicable mode, including horizontal/vertical writing and RTL/LTR; the full reader suite also checks EPUB TOC, visible text, selection, save/reload behavior, and PDF navigation/zoom/restore.

## Checks run

All checks below ran on the candidate worktree on 2026-10-31 JST:

- `npm --prefix frontend test -- src/lib/public-demo-credentials.test.ts src/pages/login.test.tsx` — 2 files, 6 tests passed.
- `npm --prefix frontend run test:readers -- --grep 'narrow odd-width spreads|height reflow|conditional publisher writing mode'` — 5 tests passed.
- `READER_ACTUAL_EPUB_PATH=<absolute generated public demo EPUB> npm --prefix frontend run test:readers -- --grep 'narrow odd-width spreads'` — 1 long-EPUB matrix passed, including 393px and 320px.
- `npm --prefix frontend run lint` — passed.
- `npm --prefix frontend run build` — passed; Vite emitted its existing large-chunk warning.
- `npm --prefix frontend run test:node` — 3 files, 9 tests passed.
- `npm --prefix frontend test` — 23 files, 110 tests passed.
- `npm --prefix frontend run test:readers` — 25 tests passed.
- `git diff --check` — passed.

These local builds did not receive production `VITE_API_BASE_URL` or the public demo credential pair; they are validation builds, not release artifacts.

## Release holds and acceptance status

- **Favicon generation: blocked.** No existing cost-bounded generation provider credential/model with a verifiable per-image cost was available. No generated image or replacement favicon is included. No new contract, top-up, or automatic spend was attempted.
- **Public demo credentials: blocked.** The existing build variables and guarded login display are present, but this session did not obtain the dedicated general demo account credentials through its approved operator path or verify that account's role and private-book/Admin denials. No credential values were introduced to source or build output.
- **i5 GUI: unverified.** The approved remote device path did not provide a usable GUI session. A normal batch SSH attempt stopped at host-key verification, and the PV3 worker probe reported a runtime identity mismatch during its approved sync. No trust/bootstrap bypass was attempted. Candidate browser tests are not physical i5 evidence.
- **Production preflight: blocked.** The fixed read-only host-summary request remained pending. The task's last supplied root-free-space report was 11,984,637,952 bytes (~11.16 GiB); a separate PV3 host-info value of 29,816,709,120 bytes was not verified as `/` free space. No fresh exact-root measurement is available. This work also did not verify the approved private DB backup and `pg_restore -l`, previous frontend image/Compose identity, the target's source/image/asset mapping, or the required rollback command. No image/archive was transferred and no service was changed.
- **Production browser, live demo login, authenticated EPUB/PDF, and canonical GitHub read-back: not run.** Existing baseline health information is not candidate deployment evidence.

No database migration, backend change, R2 operation, rollback, or deployment was performed. Do not promote this candidate until the existing approved image-generation, demo-credential, i5 GUI, backup/rollback, capacity, normal review/CI, and production operator gates are satisfied. Keep this candidate distinct from the owner-reported `f8d69c7` baseline.
