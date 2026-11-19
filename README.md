# ao3html2epub

Converts an AO3 fic that you downloaded as HTML into an EPUB.

Since the great outage of October 2026, AO3 only offers downloads as HTML. This script turns those files into
e-books, much like AO3's own EPUB downloads used to be.

- the title page has the tags, summary, author's notes and a link to the work on AO3
- the book ends with a link back to the work, so you can find your way back to leave kudos and comments

> **Please do go back and leave kudos and comments on the original fic. Writers love hearing from readers!!!**

## You need

- Python 3.9 or newer (on a Mac it's usually already there: run `python3 --version` to check.
  On Windows, get it from [python.org](https://www.python.org/downloads/))
- An AO3 story saved with Download > HTML (a work page saved from your browser with **Entire Work** also works)
- [`html2epub.py`](html2epub.py) from this repository

## Use

Open a terminal in the folder where you saved `html2epub.py`.

One story:

```
python3 html2epub.py "My Story.html"
```

A whole folder:

```
python3 html2epub.py "my-folder"
```

On Windows, type `python` instead of `python3`.

The EPUBs appear in a new `epub` folder next to your HTML. Files in the folder that aren't AO3 stories are skipped.

To put them somewhere else, add `-o "folder"`:

```
python3 html2epub.py "My Story.html" -o "Books"
```

## Good to know

- Images in stories are left out.
- Recent Kindles can read EPUBs sent with [Send to Kindle](https://www.amazon.com/sendtokindle).

## License

MIT. See [LICENSE](LICENSE).
