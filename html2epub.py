#!/usr/bin/env python3
"""html2epub: turn AO3 HTML downloads into EPUB e-books.

    python3 html2epub.py story.html             -> epub/<Title> - <Author>.epub
    python3 html2epub.py my-folder              -> every .html file in the folder
    python3 html2epub.py my-folder -o ~/Books   -> choose where the EPUBs go

Works with AO3's "Download > HTML" files and with work pages saved from the
browser. Needs Python 3.9 or newer and nothing else. Keeps the tags, summaries
and author notes, and links back to the work on AO3. Images are left out.
"""
import argparse
import html
import re
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

sys.setrecursionlimit(10000)  # badly nested HTML can get very deep


# --------------------------------------------------------------------------- #
# Reading HTML into a small tree
# --------------------------------------------------------------------------- #

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}
CLOSES_P = {'p', 'div', 'ul', 'ol', 'dl', 'blockquote', 'pre', 'hr', 'table', 'center',
            'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}


class Node:
    def __init__(self, tag, attrs=(), parent=None):
        self.tag = tag
        self.attrs = {k: v or '' for k, v in attrs}
        self.cls = set(self.attrs.get('class', '').lower().split())
        self.kids = []  # child Nodes and text strings
        self.parent = parent

    def get(self, key):
        return self.attrs.get(key, '')

    def elements(self):
        """Every element below this one, in document order."""
        stack = [iter(self.kids)]
        while stack:
            for k in stack[-1]:
                if isinstance(k, Node):
                    yield k
                    stack.append(iter(k.kids))
                    break
            else:
                stack.pop()

    def find(self, test):
        return next((n for n in self.elements() if test(n)), None)

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent

    def previous(self):
        """The element just before this one, at the same level."""
        prev = None
        for k in self.parent.kids:
            if k is self:
                return prev
            if isinstance(k, Node):
                prev = k


class TreeBuilder(HTMLParser):
    """Builds a Node tree, forgiving the usual sloppy HTML (unclosed <p>, <li>...)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = Node('#root')

    def handle_starttag(self, tag, attrs):
        if self.cur.tag == 'p' and tag in CLOSES_P:
            self.cur = self.cur.parent
        if (tag == 'li' and self.cur.tag == 'li') or (tag in ('dt', 'dd') and self.cur.tag in ('dt', 'dd')):
            self.cur = self.cur.parent
        node = Node(tag, attrs, self.cur)
        self.cur.kids.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.kids.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        for n in [self.cur, *self.cur.ancestors()]:
            if n.tag == tag:
                self.cur = n.parent
                return

    def handle_data(self, data):
        self.cur.kids.append(data)


def parse_html(path):
    data = path.read_bytes()
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = data.decode('cp1252', errors='replace')
    builder = TreeBuilder()
    builder.feed(text)
    builder.close()
    return builder.root, text


# --------------------------------------------------------------------------- #
# Text and XHTML output
# --------------------------------------------------------------------------- #

SKIP = {'script', 'style', 'head', 'noscript', 'svg', 'iframe', 'object', 'embed', 'form', 'button',
        'select', 'textarea', 'input', 'img', 'picture', 'video', 'audio', 'canvas', 'template'}


def hidden(n):
    return (n.tag in SKIP or 'landmark' in n.cls or 'hidden' in n.attrs
            or 'display:none' in n.get('style').replace(' ', '').lower())


def text(n):
    """The visible text of a node, whitespace collapsed."""
    def raw(x):
        for k in x.kids:
            if isinstance(k, str):
                yield k
            elif k.tag == 'br':
                yield ' '
            elif not hidden(k):
                yield from raw(k)
    return re.sub(r'\s+', ' ', ''.join(raw(n))).strip()


_NOT_XML = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]')


def esc(s, quote=False):
    return _NOT_XML.sub('', html.escape(s, quote))


KEEP = {'p', 'div', 'blockquote', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li',
        'dl', 'dt', 'dd', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'caption',
        'em', 'i', 'strong', 'b', 'u', 's', 'sup', 'sub', 'small', 'code'}
RENAME = {'cite': 'i', 'dfn': 'i', 'var': 'i', 'ins': 'u', 'strike': 's', 'del': 's',
          'tt': 'code', 'kbd': 'code', 'samp': 'code', 'center': 'div', 'section': 'div',
          'article': 'div', 'header': 'div', 'footer': 'div', 'main': 'div', 'aside': 'div',
          'figure': 'div', 'figcaption': 'div', 'address': 'div', 'details': 'div', 'summary': 'div'}
STYLES = [('i', r'font-style:italic'), ('b', r'font-weight:(bold|[6-9]00)'),
          ('u', r'text-decoration:underline'), ('s', r'text-decoration:line-through')]


def centered(n):
    style = n.get('style').replace(' ', '').lower()
    return n.tag == 'center' or n.get('align').lower() == 'center' or 'text-align:center' in style


def to_xhtml(nodes):
    """Rewrite HTML nodes as clean XHTML, keeping only simple formatting tags."""
    out = []
    for k in nodes:
        if isinstance(k, str):
            out.append(esc(k))
            continue
        if hidden(k):
            continue
        tag = RENAME.get(k.tag, k.tag)
        if tag in ('br', 'hr'):
            out.append('<%s />' % tag)
            continue
        inner = to_xhtml(k.kids)
        href = k.get('href').strip()
        if tag == 'a' and re.match(r'https?://', href, re.I):
            out.append('<a href="%s">%s</a>' % (esc(href, True), inner))
        elif tag in KEEP:
            cls = ' class="c"' if tag in ('p', 'div') and centered(k) else ''
            out.append('<%s%s>%s</%s>' % (tag, cls, inner, tag))
        else:  # span, font, local links...: keep the contents, plus any italic/bold styling
            style = k.get('style').replace(' ', '').lower()
            for wrap, pattern in STYLES:
                if re.search(pattern, style):
                    inner = '<%s>%s</%s>' % (wrap, inner, wrap)
            out.append(inner)
    return ''.join(out)


# --------------------------------------------------------------------------- #
# Finding the story in an AO3 page
# --------------------------------------------------------------------------- #

class NotAStory(ValueError):
    pass


def no_notes():
    return {'summary': [], 'notes': [], 'endnotes': []}


class Work:
    def __init__(self):
        self.title = 'Untitled'
        self.author = 'Anonymous'
        self.lang = 'en'
        self.source = ''        # link to the work on AO3
        self.tags = []          # [(name, value)]
        self.notes = no_notes()  # work summary, notes and end notes: lists of Nodes
        self.chapters = []


class Chapter:
    def __init__(self, story):
        self.story = story      # Node holding the chapter text
        self.title = ''
        self.notes = no_notes()  # chapter summary, notes and end notes


def by_id(root, ident):
    return root.find(lambda n: n.get('id') == ident)


def note_kind(n):
    """Is this block a 'summary', 'notes' (before the text) or 'endnotes' (after it)? None if neither."""
    for a in n.ancestors():         # saved web pages: <div class="summary">, <div class="end notes">...
        ident = a.get('id').lower()
        if 'summary' in a.cls:
            return 'summary'
        if 'endnotes' in ident or ident == 'afterword' or {'end', 'notes'} <= a.cls:
            return 'endnotes'
        if 'notes' in a.cls or ident == 'notes':
            return 'notes'
    prev = n.previous()             # AO3 downloads: a label like "Chapter End Notes" just before
    label = text(prev).lower() if prev is not None else ''
    if re.fullmatch(r'(chapter |work )?(summary|notes|end notes):?', label):
        return 'summary' if 'summary' in label else 'endnotes' if 'end' in label else 'notes'
    return None


def note_blocks(scope, stories):
    """(kind, Node) for each summary or note block in scope."""
    found = []
    for n in scope.elements():
        if n.tag not in ('div', 'blockquote') or 'userstuff' not in n.cls or n in stories:
            continue
        if any(a in stories or a in found for a in n.ancestors()):
            continue
        kind = note_kind(n)
        if kind:
            found.append(n)
            yield kind, n


def is_story(n, box):
    """A <div class="userstuff"> holding chapter text (not a summary or note)."""
    if n.tag != 'div' or 'userstuff' not in n.cls:
        return False
    for a in n.ancestors():
        if a is box:
            return True
        if a.cls & {'userstuff', 'notes', 'summary', 'meta'} or a.get('id') in ('preface', 'afterword'):
            return False
    return True


def is_heading(n):
    return n.tag in ('h2', 'h3') and 'title' in n.cls or n.tag == 'h2' and 'heading' in n.cls


def chapter_parts(story, stories):
    """The elements around a chapter's text that hold its title and notes."""
    for a in story.ancestors():
        if a.get('id').startswith('chapter-'):      # saved web page: all in one <div id="chapter-N">
            return [a]
    parts = []                                       # AO3 download: a header before the text...
    prev = story.previous()
    if prev is not None and 'meta' in prev.cls:
        parts.append(prev)
    siblings = [k for k in story.parent.kids if isinstance(k, Node)]
    for sib in siblings[siblings.index(story) + 1:]:  # ...and end notes after it, up to the next chapter
        if sib in stories or sib.get('id') in ('preface', 'afterword') or sib.find(is_heading):
            break
        parts.append(sib)
    return parts


def find_chapters(root):
    box = by_id(root, 'chapters')
    if box is not None:
        stories = [n for n in box.elements() if is_story(n, box)] or [box]
    else:               # no #chapters: look for story blocks anywhere on the page
        stories = [n for n in root.elements() if is_story(n, root)]
    chapters = []
    for story in stories:
        ch = Chapter(story)
        for part in chapter_parts(story, stories):
            h = part if is_heading(part) else part.find(is_heading)
            if h is not None and not ch.title:
                ch.title = text(h)
            for kind, n in note_blocks(part, stories):
                ch.notes[kind].append(n)
        if text(story):
            chapters.append(ch)
    return chapters, stories


WARNINGS = ['Creator Chose Not To Use Archive Warnings', 'No Archive Warnings Apply', 'Graphic Depictions Of Violence',
            'Major Character Death', 'Rape/Non-Con', 'Underage Sex']


def read_tags(root):
    """The work's tags and stats as [(name, value)], e.g. ('Fandom', 'A, B')."""
    dl = root.find(lambda n: n.tag == 'dl' and ('tags' in n.cls or {'work', 'meta'} <= n.cls))
    pairs, key = [], None
    for n in (dl.elements() if dl is not None else []):
        if n.tag == 'dt':
            key = text(n).rstrip(':').strip()
        elif n.tag == 'dd' and key:
            if n.find(lambda m: m.tag == 'dl') is None:   # a saved page's 'Stats' holds its own dt/dd list
                items = [text(li) for li in n.elements() if li.tag == 'li']
                value = ', '.join(items) if items else text(n)
                if key.lower() == 'stats':                # AO3 download: "Published: 2024-01-02 Words: 25 ..."
                    pairs += re.findall(r'(\w+):\s*(.*?)(?=\s+\w+:|$)', value)
                else:
                    pairs.append((key, value))
            key = None

    tags = []
    for key, value in pairs:
        value = ', '.join(dict.fromkeys(v for v in value.split(', ') if v))   # drop repeats
        warnings = [w for w in WARNINGS if w in value]
        if warnings and not re.sub('|'.join(map(re.escape, warnings)), '', value).strip(' ,'):
            value = ', '.join(warnings)        # also untangles "...ViolenceMajor Character Death"
        if value and key.lower() not in ('kudos', 'hits', 'comments', 'bookmarks', 'source', 'original'):
            tags.append((key, value))
    return tags


LANGS = {
    'english': 'en', 'español': 'es', 'français': 'fr', 'deutsch': 'de', 'italiano': 'it',
    'português brasileiro': 'pt-BR', 'português europeu': 'pt-PT', 'português': 'pt', 'русский': 'ru',
    'polski': 'pl', 'nederlands': 'nl', 'svenska': 'sv', 'norsk': 'no', 'dansk': 'da', 'suomi': 'fi',
    'čeština': 'cs', 'magyar': 'hu', 'română': 'ro', 'türkçe': 'tr', 'ελληνικά': 'el', 'українська': 'uk',
    'български': 'bg', 'hrvatski': 'hr', 'српски': 'sr', 'català': 'ca', 'euskara': 'eu', 'galego': 'gl',
    'עברית': 'he', 'العربية': 'ar', 'فارسی': 'fa', 'हिन्दी': 'hi', 'বাংলা': 'bn', 'ไทย': 'th',
    'tiếng việt': 'vi', 'bahasa indonesia': 'id', 'bahasa malaysia': 'ms', 'filipino': 'fil',
    '中文-普通话 國語': 'zh', '中文': 'zh', '日本語': 'ja', '한국어': 'ko', 'latina': 'la', 'esperanto': 'eo',
    'eald englisċ': 'ang', 'middel englisch': 'enm',
}


def language(root, tags):
    name = dict(tags).get('Language', '').lower()
    if name:
        return LANGS.get(name, 'und')
    page = root.find(lambda n: n.tag == 'html')
    code = page.get('lang').strip() if page is not None else ''
    return code if re.fullmatch(r'[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*', code) else 'en'


def read_work(path):
    root, raw = parse_html(path)
    work = Work()
    work.chapters, stories = find_chapters(root)
    if not work.chapters:
        raise NotAStory('no AO3 story found')

    # Summary and notes that don't belong to a chapter belong to the whole work
    in_chapters = [n for ch in work.chapters for kind in ch.notes for n in ch.notes[kind]]
    for kind, n in note_blocks(root, stories):
        if n not in in_chapters:
            work.notes[kind].append(n)

    preface = by_id(root, 'preface') or root
    h = root.find(lambda n: n.tag == 'h2' and {'title', 'heading'} <= n.cls) or \
        preface.find(lambda n: n.tag == 'h1' and any('meta' in a.cls for a in n.ancestors()))
    page_title = root.find(lambda n: n.tag == 'title')
    page_title = re.sub(r'\s*\[Archive of Our Own\]$', '', text(page_title)) if page_title is not None else ''
    work.title = (text(h) if h is not None else '') or page_title or 'Untitled'

    byline = root.find(lambda n: 'byline' in n.cls)
    if byline is not None:
        names = list(dict.fromkeys(text(a) for a in byline.elements() if a.tag == 'a' and text(a)))
        m = re.search(r'\bby\s+(.+)', text(byline), re.I)
        work.author = ', '.join(names) or (m.group(1) if m else text(byline)) or 'Anonymous'

    work.tags = read_tags(root)
    work.lang = language(root, work.tags)
    m = re.search(r'https?://(?:www\.)?(?:archiveofourown\.(?:org|com|net)|ao3\.org)/works/(\d+)', raw, re.I)
    work.source = 'https://archiveofourown.org/works/' + m.group(1) if m else ''
    return work


# --------------------------------------------------------------------------- #
# Writing the EPUB
# --------------------------------------------------------------------------- #

CSS = """body{font-family:serif;line-height:1.5;margin:5%}
h1{text-align:center;font-size:1.6em;margin:1.2em 0 .2em}
h2{text-align:center;font-size:1.25em;margin:1.4em 0 .8em}
h3,h4,h5,h6{font-size:1.05em;margin:1.2em 0 .6em}
p{margin:0 0 .85em}
.c{text-align:center}
blockquote{margin:.8em 1.5em}
pre{white-space:pre-wrap;font-size:.9em}
.byline{text-align:center;font-style:italic;margin-bottom:1.5em}
.meta{border:1px solid #999;padding:.6em .9em;margin:1em 0;font-size:.9em}
.meta p{margin:.25em 0}
.message{text-align:center;font-size:.9em;margin:1em 0}
.notes{margin:1em 0 1.5em}
.label{font-weight:bold;margin-bottom:.3em}
hr{border:0;border-top:1px solid #999;margin:1.4em 20%}
"""

ARCHIVE = '<a href="https://archiveofourown.org/">Archive of Our Own</a>'



def page(title, body, lang):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            'xml:lang="%s" lang="%s">\n<head><title>%s</title>'
            '<link rel="stylesheet" type="text/css" href="style.css" /></head>\n<body>\n%s\n</body>\n</html>\n'
            % (lang, lang, esc(title), body))


def notes_html(label, nodes):
    return ''.join('<div class="notes"><p class="label">%s</p>\n%s\n</div>\n' % (label, to_xhtml(n.kids))
                   for n in nodes)


def write_epub(work, dest):
    link = esc(work.source, True)

    # Title page: title, author, link to AO3, tags, summary, notes
    body = '<h1>%s</h1>\n<p class="byline">by %s</p>\n' % (esc(work.title), esc(work.author))
    if work.source:
        body += '<p class="message">Posted originally on the %s at <a href="%s">%s</a>.</p>\n' % (ARCHIVE, link, link)
    if work.tags:
        body += '<div class="meta">%s</div>\n' % ''.join('<p><b>%s:</b> %s</p>' % (esc(k), esc(v)) for k, v in work.tags)
    body += notes_html('Summary', work.notes['summary']) + notes_html('Notes', work.notes['notes'])
    files = {'style.css': CSS, 'title.xhtml': page(work.title, body, work.lang)}
    toc = [('Title page', 'title.xhtml')]

    # One file per chapter, with its own summary and notes
    multi = len(work.chapters) > 1
    word = 'Chapter ' if multi else ''
    for i, ch in enumerate(work.chapters, 1):
        label = ch.title or ('Chapter %d' % i if multi else 'Story')
        body = '<h2>%s</h2>\n' % esc(label) if multi else ''
        body += notes_html(word + 'Summary', ch.notes['summary']) + notes_html(word + 'Notes', ch.notes['notes'])
        body += to_xhtml(ch.story.kids) + '\n'
        if ch.notes['endnotes']:
            body += '<hr />\n' + notes_html(word + 'End Notes', ch.notes['endnotes'])
        name = 'chapter-%d.xhtml' % i
        files[name] = page(label, body, work.lang)
        toc.append((label, name))

    # Afterword: the work's end notes, and a nudge to leave kudos and comments on AO3
    body = notes_html('End Notes', work.notes['endnotes'])
    if work.source:
        body += ('<p class="message">Please <a href="%s">drop by the Archive</a> and leave kudos or a '
                 '<a href="%s/comments/new">comment</a> to let the creator know if you enjoyed their work!</p>\n'
                 % (link, link))
    if body:
        files['afterword.xhtml'] = page('Afterword', '<h2>Afterword</h2>\n' + body, work.lang)
        toc.append(('Afterword', 'afterword.xhtml'))

    # Table of contents (nav.xhtml for EPUB 3 readers, toc.ncx for older ones)
    uid = 'urn:uuid:%s' % uuid.uuid5(uuid.NAMESPACE_URL, work.source or work.title + work.author)
    files['nav.xhtml'] = page('Contents', '<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>%s</ol></nav>' % ''.join(
        '<li><a href="%s">%s</a></li>' % (f, esc(label)) for label, f in toc), work.lang)
    files['toc.ncx'] = (
        '<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        '<head><meta name="dtb:uid" content="%s"/></head><docTitle><text>%s</text></docTitle><navMap>%s</navMap></ncx>'
        % (uid, esc(work.title), ''.join(
            '<navPoint id="n%d" playOrder="%d"><navLabel><text>%s</text></navLabel><content src="%s"/></navPoint>'
            % (i, i, esc(label), f) for i, (label, f) in enumerate(toc, 1))))

    # Package file: book metadata, file list and reading order
    tags = dict(work.tags)
    subjects = []
    for key in tags:
        if key.lower().startswith(('fandom', 'categor', 'relationship', 'additional tag')):
            subjects += [s.strip() for s in tags[key].split(',') if s.strip()]
    extra = ''.join('<dc:subject>%s</dc:subject>' % esc(s) for s in list(dict.fromkeys(subjects))[:40])
    if work.source:
        extra += '<dc:source>%s</dc:source>' % esc(work.source)
    if work.notes['summary']:
        extra += '<dc:description>%s</dc:description>' % esc(' '.join(map(text, work.notes['summary']))[:1500])
    files['content.opf'] = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="bookid">%s</dc:identifier>'
        '<dc:title>%s</dc:title><dc:creator>%s</dc:creator><dc:language>%s</dc:language>%s'
        '<meta property="dcterms:modified">%s</meta></metadata>'
        '<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        '<item id="css" href="style.css" media-type="text/css"/>%s</manifest><spine toc="ncx">%s</spine></package>'
        % (uid, esc(work.title), esc(work.author), work.lang, extra,
           datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           ''.join('<item id="p%d" href="%s" media-type="application/xhtml+xml"/>' % (i, f) for i, (_, f) in enumerate(toc)),
           ''.join('<itemref idref="p%d"/>' % i for i in range(len(toc)))))

    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml',
                   '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                   '</rootfiles></container>')
        for name, data in files.items():
            z.writestr('OEBPS/' + name, data)


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #

def safe_filename(s):
    s = re.sub(r'\s+', ' ', re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', s)).strip(' .')
    return s[:110].strip(' .') or 'book'


def html_files(inputs):
    files = []
    for arg in inputs:
        p = Path(arg).expanduser()
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in ('.html', '.htm') and not f.name.startswith('.'))
        elif p.is_file():
            files.append(p)
        else:
            print('! not found: %s' % arg, file=sys.stderr)
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(prog='html2epub', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('inputs', nargs='+', help='AO3 HTML files and/or folders of them')
    ap.add_argument('-o', '--out', help='output folder (default: an "epub" folder next to the input)')
    args = ap.parse_args(argv)

    files = html_files(args.inputs)
    if not files:
        ap.error('no HTML files found')
    if args.out:
        out_dir = Path(args.out).expanduser()
    else:
        first = Path(args.inputs[0]).expanduser()
        out_dir = (first if first.is_dir() else first.parent) / 'epub'

    made = failed = 0
    used = set()
    for f in files:
        try:
            work = read_work(f)
            name = base = safe_filename('%s - %s' % (work.title, work.author))
            n = 2
            while name.lower() in used:     # two works with the same title and author
                name, n = '%s (%d)' % (base, n), n + 1
            used.add(name.lower())
            dest = out_dir / (name + '.epub')
            write_epub(work, dest)
        except NotAStory as e:
            print('skip  %s: %s' % (f.name, e))
            continue
        except Exception as e:
            failed += 1
            print('FAIL  %s: %s' % (f.name, e), file=sys.stderr)
            continue
        made += 1
        print('ok    %s  (%d chapter%s)' % (dest.name, len(work.chapters), '' if len(work.chapters) == 1 else 's'))
    print('%d EPUB%s written to %s%s' % (made, '' if made == 1 else 's', out_dir,
                                         '  (%d failed)' % failed if failed else ''))
    return 1 if failed or not made else 0


if __name__ == '__main__':
    sys.exit(main())
