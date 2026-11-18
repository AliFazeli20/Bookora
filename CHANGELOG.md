
# Changelog

All notable changes to this project will be documented in this file.

## [0.3.0] - 2026-11-18

### Added
- **Quotes section** (new page in the sidebar): save the sentences you love from your books, each with
  the book, an optional page number and your own note. Quotes are searchable (text, note, book, author).
- **Quote of the day** at the top of the page (the same quote all day), with an "Another" button.
- Favorite quotes (heart), and a filter for all quotes, favorites or a single book. Sort by newest,
  oldest, by book, or favorites first.
- Add / edit quote dialog with a book picker that finds a book anywhere in its title. "Save & add
  another" keeps the book and the page, so a whole evening of quotes can be typed in a row.
  Duplicate quotes in the same book are refused.
- Copy a quote together with its author, book and page.
- Make an image of a quote to share: 6 styles (Midnight, Paper, Light, Ocean, Violet, Cover), 3 sizes
  (Square, Story, Wide), copy it or save it as PNG. Drawn with QPainter, no extra library.
- Import and export quotes as CSV in Data & backup. Import is all-or-nothing, makes a safety backup
  first, and lists the books that were not found in your library.
- "Only by author" filter in Find online (Book title searches): keeps only books by that author,
  accepts close spellings (Dostoevsky / Dostoyevsky) and Persian names (the English name is looked up
  on Wikidata, so Open Library and Apple Books can search too).
- Quote cards and quote images support right-to-left text (Persian), including verse with line breaks.

### Changed
- Deleting a book that has quotes asks whether to delete the quotes too or keep them. Undo brings back
  the book together with the quotes that were deleted with it.
- Renaming a book or changing its author updates its quotes.
- The CSV buttons in Data & backup are now named "books" and "quotes".

### Database
- New `quotes` table, created automatically on first start. The `books` and `wishlist` tables are not
  changed, and existing libraries and backups keep working.

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

[0.3.0]: https://github.com/AliFazeli20/Bookora/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/AliFazeli20/Bookora/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/AliFazeli20/Bookora/releases/tag/v0.1.0
