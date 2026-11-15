# PDF and Markdown side by side (short design)

Status: approved by the owner 2026-11-15 ("yes to all three").

## Intent

After a PDF is converted with **Convert**, a person checks the Markdown against the original. Today they flip
between **PDF** and **MD**. Side by side shows both at once, on the same page.

## Approaches

1. **A third choice on the existing switch: PDF | Both | MD** (chosen). Both views already follow the reader's
   one current page, so the halves stay in step with no new state.
2. A separate window or tab for the Markdown. Rejected: two windows can't share the current page without new
   plumbing, and a phone gains nothing.
3. Render every PDF page in a scrolling column beside the Markdown. Rejected: the reader draws one PDF page on
   one canvas; a second renderer is a larger change than the wish needs.

## Design

- `viewMode` for a document gains `'both'` beside `'pdf'` and `'md'` (per document, as today, in `App.jsx`).
- With **Both**, the document area is two columns of equal width: the PDF page on the left, the Markdown on the
  right. Each column scrolls on its own.
- Same page in both directions, through the existing `currentPage`: turning the PDF page scrolls the Markdown to
  that page's heading; scrolling the Markdown turns the PDF page.
- Zoom applies to both, as it does today to each view.
- Phone layout (`effectiveIsMobile`): **Both** is not offered, and a document left on `'both'` shows the PDF.
- Only for a converted PDF (`convertState === 'converted'`), like the existing switch.
- With the chat panel open beside the reader, **Both** still works; each column is narrower.

## Non-goals

- Converting bulk-uploaded PDFs automatically. They have no Markdown until someone presses **Convert**.
- Sentence-level alignment between the two halves. Alignment is by page.

## Left to the builder

Column markup and class names in `PdfViewer.jsx`; tests in `PdfViewer.sideBySide.test.jsx`.
