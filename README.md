# ao3html2epub

Converts an AO3 HTML download into an EPUB.

## You need

- Python 3.9 or newer
- An AO3 story saved with Download > HTML

## Use

One story:

```
python3 html2epub.py "My Story.html"
```

A whole folder:

```
python3 html2epub.py "my-folder"
```

On Windows, type `python` instead of `python3`.

The EPUBs appear in a new `epub` folder next to your HTML. 

To put them somewhere else, add `-o "folder"`:

```
python3 html2epub.py "My Story.html" -o "Books"
```

