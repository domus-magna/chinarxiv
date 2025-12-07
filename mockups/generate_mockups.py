#!/usr/bin/env python3
"""Generate HTML mockups for different homepage design options."""

import json
from pathlib import Path
from datetime import datetime

# Read paper data
with open('../site/search-index.json') as f:
    papers = json.load(f)[:8]  # Use first 8 papers

# Helper function to format date
def format_date(date_str):
    dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    return dt.strftime('%b %d, %Y')

# ChinaRxiv color scheme
colors = {
    'primary': '#b31b1b',
    'secondary': '#2c3e50',
    'text': '#222',
    'text_muted': '#666',
    'border': '#e9ecef',
    'bg': '#fff',
    'bg_light': '#f8f9fa'
}

# Common header for all mockups
header_html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - ChinaRxiv Homepage Mockup</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: 'Charter', 'Bitstream Charter', 'Sitka Text', Cambria, serif;
            line-height: 1.6;
            color: {colors[text]};
            background: {colors[bg]};
            padding: 2rem;
            max-width: 1000px;
            margin: 0 auto;
        }}
        a {{
            color: {colors[primary]};
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
        .page-header {{
            background: {colors[primary]};
            color: white;
            padding: 1.5rem 2rem;
            margin: -2rem -2rem 2rem -2rem;
        }}
        .page-header h1 {{
            font-size: 1.8rem;
            margin-bottom: 0.5rem;
        }}
        .page-header p {{
            opacity: 0.9;
            font-size: 0.9rem;
        }}
        .back-link {{
            display: inline-block;
            margin-bottom: 1rem;
            color: {colors[text_muted]};
            font-size: 0.9rem;
        }}
'''

footer_html = '''
    </style>
</head>
<body>
    <div class="page-header">
        <h1>ChinaRxiv</h1>
        <p>{subtitle}</p>
    </div>
    <a href="index.html" class="back-link">← Back to comparison</a>
'''

# =========== OPTION 1: TIMELINE/FEED ===========
timeline_css = '''
        .timeline-feed {
            margin-top: 2rem;
        }
        .date-marker {
            background: ''' + colors['bg_light'] + ''';
            padding: 0.5rem 1rem;
            font-weight: 600;
            font-size: 0.9rem;
            color: ''' + colors['text_muted'] + ''';
            margin: 1.5rem 0 0.5rem 0;
            border-radius: 3px;
        }
        .feed-item {
            border-bottom: 1px solid ''' + colors['border'] + ''';
            padding: 1.2rem 0;
        }
        .feed-item:last-child {
            border-bottom: none;
        }
        .feed-title {
            font-size: 1.2rem;
            margin-bottom: 0.4rem;
            font-weight: 600;
        }
        .feed-meta {
            font-size: 0.85rem;
            color: ''' + colors['text_muted'] + ''';
            margin-bottom: 0.5rem;
        }
        .feed-meta a {
            color: ''' + colors['primary'] + ''';
        }
        .feed-abstract {
            font-size: 0.95rem;
            color: #444;
            line-height: 1.6;
        }
        .separator {
            margin: 0 0.5rem;
        }
'''

def generate_timeline():
    html = header_html.format(title="Option 1: Timeline", colors=colors) + timeline_css
    html += footer_html.format(subtitle="Option 1: Timeline/Feed Style")
    html += '<div class="timeline-feed">\n'

    current_date = None
    for paper in papers:
        paper_date = format_date(paper['date'])
        if paper_date != current_date:
            html += f'    <div class="date-marker">{paper_date}</div>\n'
            current_date = paper_date

        html += '    <article class="feed-item">\n'
        html += f'        <h3 class="feed-title"><a href="#">{paper["title"]}</a></h3>\n'

        subjects = paper['subjects'].split(', ')[:2]
        meta_parts = [paper['authors'], ' · '.join(subjects)]
        if paper['pdf_url']:
            meta_parts.append('<a href="#">PDF</a>')

        html += f'        <div class="feed-meta">{" · ".join(meta_parts)}</div>\n'
        html += f'        <p class="feed-abstract">{paper["abstract"][:200]}...</p>\n'
        html += '    </article>\n'

    html += '</div>\n</body>\n</html>'
    return html

# =========== OPTION 2: MAGAZINE ===========
magazine_css = '''
        .featured-paper {
            background: linear-gradient(135deg, ''' + colors['bg_light'] + ''' 0%, #fff 100%);
            border: 2px solid ''' + colors['border'] + ''';
            border-radius: 8px;
            padding: 2rem;
            margin-bottom: 2rem;
        }
        .featured-paper h2 {
            font-size: 1.8rem;
            margin-bottom: 0.8rem;
            color: ''' + colors['secondary'] + ''';
        }
        .featured-meta {
            font-size: 0.9rem;
            color: ''' + colors['text_muted'] + ''';
            margin-bottom: 1rem;
        }
        .featured-abstract {
            font-size: 1rem;
            line-height: 1.7;
            margin-bottom: 1rem;
        }
        .featured-links a {
            display: inline-block;
            background: ''' + colors['primary'] + ''';
            color: white;
            padding: 0.5rem 1.5rem;
            border-radius: 4px;
            margin-right: 0.5rem;
        }
        .recent-header {
            font-size: 1.2rem;
            margin: 2rem 0 1rem 0;
            padding-bottom: 0.5rem;
            border-bottom: 2px solid ''' + colors['border'] + ''';
        }
        .compact-list {
            display: flex;
            flex-direction: column;
            gap: 0.8rem;
        }
        .compact-item {
            display: grid;
            grid-template-columns: 2fr 1.5fr 1fr 60px;
            gap: 1rem;
            padding: 0.8rem;
            border: 1px solid ''' + colors['border'] + ''';
            border-radius: 4px;
            font-size: 0.9rem;
        }
        .compact-item:hover {
            background: ''' + colors['bg_light'] + ''';
        }
        .compact-title {
            font-weight: 600;
        }
        .compact-authors {
            color: ''' + colors['text_muted'] + ''';
        }
        .compact-date {
            color: ''' + colors['text_muted'] + ''';
            font-size: 0.85rem;
        }
'''

def generate_magazine():
    html = header_html.format(title="Option 2: Magazine", colors=colors) + magazine_css
    html += footer_html.format(subtitle="Option 2: Magazine/Featured Style")

    # Featured paper (first one)
    featured = papers[0]
    html += '<div class="featured-paper">\n'
    html += f'    <h2>{featured["title"]}</h2>\n'
    html += f'    <div class="featured-meta">{featured["authors"]} · {format_date(featured["date"])} · {featured["subjects"]}</div>\n'
    html += f'    <p class="featured-abstract">{featured["abstract"]}</p>\n'
    html += '    <div class="featured-links">\n'
    html += '        <a href="#">Abstract</a>\n'
    if featured['pdf_url']:
        html += '        <a href="#">PDF</a>\n'
    html += '    </div>\n'
    html += '</div>\n'

    # Recent papers (rest)
    html += '<h3 class="recent-header">Recent Papers</h3>\n'
    html += '<div class="compact-list">\n'
    for paper in papers[1:]:
        html += '    <div class="compact-item">\n'
        html += f'        <div class="compact-title"><a href="#">{paper["title"]}</a></div>\n'
        html += f'        <div class="compact-authors">{paper["authors"].split(", ")[0]} et al.</div>\n'
        html += f'        <div class="compact-date">{format_date(paper["date"])}</div>\n'
        pdf_link = '<a href="#">PDF</a>' if paper["pdf_url"] else ""
        html += f'        <div>{pdf_link}</div>\n'
        html += '    </div>\n'
    html += '</div>\n</body>\n</html>'
    return html

# =========== OPTION 3: COMPACT LIST ===========
compact_css = '''
        .compact-paper {
            margin-bottom: 1.8rem;
            padding-bottom: 1.2rem;
            border-bottom: 1px solid ''' + colors['border'] + ''';
        }
        .compact-paper:last-child {
            border-bottom: none;
        }
        .compact-title {
            font-size: 1.15rem;
            margin-bottom: 0.3rem;
            font-weight: 600;
            color: ''' + colors['secondary'] + ''';
        }
        .compact-meta {
            font-size: 0.85rem;
            color: ''' + colors['text_muted'] + ''';
            margin-bottom: 0.4rem;
        }
        .compact-meta a {
            color: ''' + colors['primary'] + ''';
        }
        .compact-abstract {
            font-size: 0.95rem;
            color: #444;
            line-height: 1.5;
        }
'''

def generate_compact():
    html = header_html.format(title="Option 3: Compact", colors=colors) + compact_css
    html += footer_html.format(subtitle="Option 3: Compact List (Dense)")

    for paper in papers:
        html += '<article class="compact-paper">\n'
        html += f'    <h3 class="compact-title"><a href="#">{paper["title"]}</a></h3>\n'

        subjects = paper['subjects'].split(', ')[:2]
        meta_parts = [paper['authors'], ' · '.join(subjects), format_date(paper['date'])]
        if paper['pdf_url']:
            meta_parts.append('<a href="#">PDF</a>')

        html += f'    <div class="compact-meta">{" · ".join(meta_parts)}</div>\n'
        html += f'    <p class="compact-abstract">{paper["abstract"][:150]}...</p>\n'
        html += '</article>\n'

    html += '</body>\n</html>'
    return html

# =========== OPTION 4: NEWSPAPER ===========
newspaper_css = '''
        .newspaper-item {
            display: grid;
            grid-template-columns: 180px 1fr;
            gap: 2rem;
            margin-bottom: 1.8rem;
            padding-bottom: 1.5rem;
            border-bottom: 1px solid ''' + colors['border'] + ''';
        }
        .newspaper-item:last-child {
            border-bottom: none;
        }
        .meta-column {
            font-size: 0.85rem;
            color: ''' + colors['text_muted'] + ''';
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }
        .meta-date {
            font-weight: 600;
            color: ''' + colors['secondary'] + ''';
        }
        .meta-subjects {
            font-size: 0.8rem;
            line-height: 1.4;
        }
        .content-column h3 {
            font-size: 1.2rem;
            margin-bottom: 0.4rem;
            font-weight: 600;
        }
        .content-authors {
            font-size: 0.9rem;
            color: ''' + colors['text_muted'] + ''';
            margin-bottom: 0.6rem;
        }
        .content-column p {
            font-size: 0.95rem;
            color: #444;
            line-height: 1.6;
        }
'''

def generate_newspaper():
    html = header_html.format(title="Option 4: Newspaper", colors=colors) + newspaper_css
    html += footer_html.format(subtitle="Option 4: Left-Aligned Meta (Newspaper)")

    for paper in papers:
        html += '<article class="newspaper-item">\n'
        html += '    <div class="meta-column">\n'
        html += f'        <div class="meta-date">{format_date(paper["date"])}</div>\n'
        html += f'        <div class="meta-subjects">{paper["subjects"]}</div>\n'
        if paper['pdf_url']:
            html += '        <a href="#">PDF</a>\n'
        html += '    </div>\n'
        html += '    <div class="content-column">\n'
        html += f'        <h3><a href="#">{paper["title"]}</a></h3>\n'
        html += f'        <div class="content-authors">{paper["authors"]}</div>\n'
        html += f'        <p>{paper["abstract"][:180]}...</p>\n'
        html += '    </div>\n'
        html += '</article>\n'

    html += '</body>\n</html>'
    return html

# =========== INDEX PAGE ===========
def generate_index():
    html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Homepage Design Comparison - ChinaRxiv</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: system-ui, -apple-system, sans-serif;
            line-height: 1.6;
            padding: 2rem;
            background: #f5f5f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        h1 {
            color: #b31b1b;
            margin-bottom: 1rem;
        }
        .intro {
            background: white;
            padding: 1.5rem;
            border-radius: 8px;
            margin-bottom: 2rem;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 1.5rem;
        }
        .option-card {
            background: white;
            border: 2px solid #e9ecef;
            border-radius: 8px;
            padding: 1.5rem;
            transition: all 0.2s;
        }
        .option-card:hover {
            border-color: #b31b1b;
            box-shadow: 0 4px 12px rgba(179, 27, 27, 0.1);
        }
        .option-card h2 {
            color: #2c3e50;
            margin-bottom: 0.5rem;
            font-size: 1.3rem;
        }
        .option-card .concept {
            color: #666;
            font-size: 0.9rem;
            margin-bottom: 1rem;
        }
        .option-card ul {
            list-style: none;
            margin-bottom: 1rem;
        }
        .option-card li {
            padding: 0.3rem 0;
            font-size: 0.9rem;
            color: #444;
        }
        .option-card li::before {
            content: "✓ ";
            color: #b31b1b;
            font-weight: bold;
        }
        .option-card a {
            display: inline-block;
            background: #b31b1b;
            color: white;
            padding: 0.6rem 1.5rem;
            border-radius: 4px;
            text-decoration: none;
        }
        .option-card a:hover {
            background: #850000;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Homepage Design Options - ChinaRxiv</h1>
        <div class="intro">
            <p><strong>Goal:</strong> Create a distinctive "What's New" homepage that emphasizes recent content and looks different from search results.</p>
            <p style="margin-top: 0.5rem;">Click each option below to view a live mockup with real paper data.</p>
        </div>

        <div class="grid">
            <div class="option-card">
                <h2>Option 1: Timeline</h2>
                <p class="concept">Clean vertical timeline emphasizing chronology</p>
                <ul>
                    <li>Date dividers separate papers by day</li>
                    <li>No boxes/borders - just horizontal dividers</li>
                    <li>High density, maximum scan-ability</li>
                </ul>
                <a href="option1-timeline.html">View Timeline →</a>
            </div>

            <div class="option-card">
                <h2>Option 2: Magazine</h2>
                <p class="concept">Hero featured paper + compact recent list</p>
                <ul>
                    <li>Top paper gets featured treatment</li>
                    <li>Rest are compact single-line items</li>
                    <li>Clear hierarchy (new = prominent)</li>
                </ul>
                <a href="option2-magazine.html">View Magazine →</a>
            </div>

            <div class="option-card">
                <h2>Option 3: Compact</h2>
                <p class="concept">Maximum density, minimal styling</p>
                <ul>
                    <li>No boxes, borders, or backgrounds</li>
                    <li>Title stands alone (bold)</li>
                    <li>Very scannable, text-focused</li>
                </ul>
                <a href="option3-compact.html">View Compact →</a>
            </div>

            <div class="option-card">
                <h2>Option 4: Newspaper</h2>
                <p class="concept">Metadata column on left, content on right</p>
                <ul>
                    <li>Two-column layout</li>
                    <li>Very distinctive from search</li>
                    <li>Efficient use of space</li>
                </ul>
                <a href="option4-newspaper.html">View Newspaper →</a>
            </div>
        </div>
    </div>
</body>
</html>'''
    return html

# Generate all files
Path('option1-timeline.html').write_text(generate_timeline())
Path('option2-magazine.html').write_text(generate_magazine())
Path('option3-compact.html').write_text(generate_compact())
Path('option4-newspaper.html').write_text(generate_newspaper())
Path('index.html').write_text(generate_index())

print("✓ Generated all mockup files:")
print("  - index.html")
print("  - option1-timeline.html")
print("  - option2-magazine.html")
print("  - option3-compact.html")
print("  - option4-newspaper.html")
