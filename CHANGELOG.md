
# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2026-11-10

### Added
- Right-click menu on book cards: edit, quick status change (Mark as Read / Start reading / Move to
  Want to read) and delete. On the wishlist it offers "Bought — move to library".
  If the new status does not fit the book's dates, the edit dialog opens with the status already chosen.
- Undo after deleting a book or a wishlist entry (a message at the bottom for 7 seconds, or Ctrl+Z),
  instead of a confirmation window. The book comes back with its original id, cover, rating, notes and dates.
- Short messages ("toasts") for backup, restore, import and export results instead of pop-up boxes.
- Empty states with a picture and the right actions: an empty library (Add book / Find online /
  Import from CSV), an empty wishlist, an empty status, and a search with no match.
- A warning before unsaved changes in the book dialog are lost (Cancel, Esc or closing the window):
  Save / Discard / Keep editing.
- Search box: clear button and Ctrl+F.
- The app remembers the theme, the sort order and the window size (and maximised state).
- A `fonts/` folder next to the app is loaded automatically, so Vazirmatn works without installing it.
- Persian digits for dates when the Jalali calendar is selected (cards, date fields, statistics year).
- Tooltip on each card with the full title, author and the start of the notes.
- The version number is shown in Data & backup.

### Changed
- Cards: larger cover with a soft blurred copy of the cover behind it, colour-coded status chips,
  titles up to two lines, and a smooth hover animation instead of a jump.
- Online search with "Show covers in the results" now uses small pictures kept in memory only: one
  download per result (the smaller size when the source offers one), at most about 6 MB kept, and nothing
  is written to `covers/`. A book that is already in your library shows its own cover without any download.
  The real cover is downloaded only when you press Add. Persian results that need a search first are
  looked up one at a time.
- Adding a book while the Reading or Read view is open starts with that status.
- Tooltips follow the dark / light theme.

### Fixed
- Primary buttons showed no change on hover.
- Buttons of the confirmation boxes had no shape on the dark theme.
- The highlighted card did not follow the mouse when the list was scrolled with the mouse wheel.

### Removed
- The old "are you sure?" delete window (replaced by Undo).

## [0.1.0] - 2026-11-07

### Added
- Initial release of My Books.
- Personal book library management.
- Book wishlist management.
- Online book search across multiple sources.
- Book cover downloading and management.
- Reading status and progress tracking.
- Book ratings and personal notes.
- Gregorian and Jalali calendar support.
- Reading statistics and yearly reading goals.
- Monthly reading activity charts.
- Automatic and manual database backups.
- Backup restoration.
- CSV import and export.
- Dark and light themes.
- Local SQLite database storage.

[0.2.0]: https://github.com/AliFazeli20/Bookora/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/AliFazeli20/Bookora/releases/tag/v0.1.0
