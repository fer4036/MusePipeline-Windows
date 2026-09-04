"""Convert the project's technical Markdown report into standalone XeLaTeX."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'REPORTE_TECNICO_PIPELINE_MUSE_ACTUAL.md'
TARGET = ROOT / 'REPORTE_TECNICO_PIPELINE_MUSE_ACTUAL.tex'

PREAMBLE = r'''\documentclass[11pt,a4paper]{article}

% Compatible con pdfLaTeX, XeLaTeX y LuaLaTeX.
\usepackage{iftex}
\ifPDFTeX
  \usepackage[T1]{fontenc}
  \usepackage[utf8]{inputenc}
  \usepackage{lmodern}
  \DeclareUnicodeCharacter{00BF}{\textquestiondown}
  \DeclareUnicodeCharacter{00D7}{\ensuremath{\times}}
  \DeclareUnicodeCharacter{00B2}{\textsuperscript{2}}
  \DeclareUnicodeCharacter{2013}{\textendash}
  \DeclareUnicodeCharacter{2014}{\textemdash}
  \DeclareUnicodeCharacter{201C}{``}
  \DeclareUnicodeCharacter{201D}{''}
  \DeclareUnicodeCharacter{2192}{\ensuremath{\rightarrow}}
  \DeclareUnicodeCharacter{2193}{\ensuremath{\downarrow}}
  \DeclareUnicodeCharacter{2500}{-}
  \DeclareUnicodeCharacter{2502}{|}
  \DeclareUnicodeCharacter{250C}{+}
  \DeclareUnicodeCharacter{2510}{+}
  \DeclareUnicodeCharacter{2514}{+}
  \DeclareUnicodeCharacter{2518}{+}
  \DeclareUnicodeCharacter{252C}{+}
  \DeclareUnicodeCharacter{2534}{+}
  \DeclareUnicodeCharacter{253C}{+}
  \DeclareUnicodeCharacter{25B2}{\ensuremath{\blacktriangle}}
  \DeclareUnicodeCharacter{25BA}{\ensuremath{\blacktriangleright}}
  \DeclareUnicodeCharacter{25BC}{\ensuremath{\blacktriangledown}}
  \DeclareUnicodeCharacter{25C4}{\ensuremath{\blacktriangleleft}}
\else
  \usepackage{fontspec}
  \defaultfontfeatures{Ligatures=TeX}
  \IfFontExistsTF{Libertinus Serif}
    {\setmainfont{Libertinus Serif}}
    {\setmainfont{Latin Modern Roman}}
  \IfFontExistsTF{DejaVu Sans Mono}
    {\setmonofont{DejaVu Sans Mono}[Scale=0.82]}
    {\setmonofont{Latin Modern Mono}[Scale=0.82]}
\fi
\usepackage{amssymb}
\usepackage[spanish,es-nodecimaldot]{babel}
\usepackage[a4paper,margin=2.4cm]{geometry}
\usepackage{microtype}
\usepackage{setspace}
\usepackage{parskip}
\usepackage{enumitem}
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{array}
\usepackage[table]{xcolor}
\usepackage{hyperref}
\usepackage{bookmark}
\usepackage{fancyhdr}
\usepackage{titlesec}
\usepackage{fvextra}
\usepackage{upquote}

\definecolor{MuseGreen}{HTML}{0B7458}
\definecolor{MuseDark}{HTML}{15231F}
\definecolor{MuseLight}{HTML}{EAF2EE}
\definecolor{CodeBackground}{HTML}{F4F6F5}
\hypersetup{
  colorlinks=true,
  linkcolor=MuseGreen,
  urlcolor=MuseGreen,
  citecolor=MuseGreen,
  pdftitle={Reporte técnico integral del pipeline Muse Research},
  pdfauthor={Proyecto Muse Research}
}

\setstretch{1.08}
\setlength{\parindent}{0pt}
\setlength{\LTpre}{0.5em}
\setlength{\LTpost}{1em}
\setlength{\LTleft}{0pt}
\setlength{\LTright}{0pt}
\renewcommand{\arraystretch}{1.22}
\setlist{nosep,leftmargin=1.8em,topsep=0.45em}
\setcounter{secnumdepth}{3}
\setcounter{tocdepth}{2}
\titleformat{\section}{\Large\bfseries\color{MuseDark}}{}{0pt}{}
\titleformat{\subsection}{\large\bfseries\color{MuseGreen}}{}{0pt}{}
\titleformat{\subsubsection}{\normalsize\bfseries\color{MuseDark}}{}{0pt}{}

\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\small Muse Research}
\fancyhead[R]{\small Reporte técnico del pipeline}
\fancyfoot[C]{\thepage}
\renewcommand{\headrulewidth}{0.3pt}

\DefineVerbatimEnvironment{MuseCode}{Verbatim}{
  breaklines=true,
  breakanywhere=true,
  fontsize=\small,
  frame=single,
  framesep=3mm,
  rulecolor=\color{MuseGreen},
  bgcolor=CodeBackground
}

\begin{document}

\begin{titlepage}
  \centering
  \vspace*{2.5cm}
  {\color{MuseGreen}\rule{\textwidth}{1.2pt}}\\[1.2cm]
  {\Huge\bfseries Reporte técnico integral\\[0.25cm]
   del pipeline Muse Research\par}
  \vspace{1cm}
  {\Large Adquisición local multimodal con Muse S Athena\par}
  \vspace{1.4cm}
  {\large EEG, IMU y PPG mediante BLE, ROS 2, SQLite y una GUI web local\par}
  \vfill
  \begin{tabular}{rl}
    \textbf{Estado documentado:} & agosto de 2026 \\
    \textbf{Plataforma:} & Ubuntu 22.04 y ROS 2 Humble \\
    \textbf{Proyecto:} & Muse Research
  \end{tabular}
  \vfill
  {\color{MuseGreen}\rule{\textwidth}{1.2pt}}
\end{titlepage}

\tableofcontents
\clearpage
'''

POSTAMBLE = r'''

\end{document}
'''


def escape_latex(text):
    """Escape ordinary LaTeX text while preserving Unicode for XeLaTeX."""
    replacements = {
        '\\': r'\textbackslash{}',
        '&': r'\&',
        '%': r'\%',
        '$': r'\$',
        '#': r'\#',
        '_': r'\_',
        '{': r'\{',
        '}': r'\}',
        '~': r'\textasciitilde{}',
        '^': r'\textasciicircum{}',
    }
    return ''.join(replacements.get(character, character) for character in text)


def inline_markup(text):
    """Convert inline code and emphasis used by the source report."""
    placeholders = []

    def save_code(match):
        token = f'LATEXCODETOKEN{len(placeholders)}ENDTOKEN'
        code = escape_latex(match.group(1))
        placeholders.append(r'{\ttfamily\small ' + code + '}')
        return token

    text = re.sub(r'`([^`]+)`', save_code, text)
    text = escape_latex(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\\textbf{\1}', text)
    text = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'\\emph{\1}', text)
    for index, value in enumerate(placeholders):
        token = escape_latex(f'LATEXCODETOKEN{index}ENDTOKEN')
        text = text.replace(token, value)
    return text


def table_rows(lines, start):
    rows = []
    index = start
    while index < len(lines) and lines[index].lstrip().startswith('|'):
        cells = [cell.strip() for cell in lines[index].strip().strip('|').split('|')]
        rows.append(cells)
        index += 1
    return rows, index


def is_separator_row(row):
    return all(re.fullmatch(r':?-{3,}:?', cell.replace(' ', '')) for cell in row)


def render_table(rows):
    if len(rows) < 2 or not is_separator_row(rows[1]):
        return [inline_markup(' | '.join(row)) for row in rows]
    header = rows[0]
    body = rows[2:]
    columns = len(header)
    width = 0.90 / max(columns, 1)
    specification = '@{}' + ''.join(
        rf'>{{\raggedright\arraybackslash}}p{{{width:.3f}\textwidth}}'
        for _ in range(columns)
    ) + '@{}'
    output = [rf'\begin{{longtable}}{{{specification}}}', r'\toprule']
    output.append(' & '.join(r'\textbf{' + inline_markup(cell) + '}' for cell in header) + r' \\')
    output.extend([r'\midrule', r'\endfirsthead', r'\toprule'])
    output.append(' & '.join(r'\textbf{' + inline_markup(cell) + '}' for cell in header) + r' \\')
    output.extend([r'\midrule', r'\endhead'])
    for row in body:
        padded = row + [''] * (columns - len(row))
        output.append(' & '.join(inline_markup(cell) for cell in padded[:columns]) + r' \\')
    output.extend([r'\bottomrule', r'\end{longtable}'])
    return output


def close_lists(output, stack):
    while stack:
        output.append(r'\end{' + stack.pop() + '}')


def convert(markdown):
    lines = markdown.splitlines()
    output = []
    paragraph = []
    lists = []
    in_code = False

    def flush_paragraph():
        if paragraph:
            output.append(inline_markup(' '.join(part.strip() for part in paragraph)))
            output.append('')
            paragraph.clear()

    index = 0
    while index < len(lines):
        line = lines[index]

        if line.startswith('```'):
            flush_paragraph()
            close_lists(output, lists)
            if in_code:
                output.extend([r'\end{MuseCode}', ''])
            else:
                output.append(r'\begin{MuseCode}')
            in_code = not in_code
            index += 1
            continue

        if in_code:
            output.append(line)
            index += 1
            continue

        if index == 0 and line.startswith('# '):
            index += 1
            continue

        if re.fullmatch(r'\s*---+\s*', line):
            flush_paragraph()
            close_lists(output, lists)
            output.extend([r'\medskip\hrule\medskip', ''])
            index += 1
            continue

        heading = re.match(r'^(#{2,4})\s+(.+)$', line)
        if heading:
            flush_paragraph()
            close_lists(output, lists)
            command = {2: 'section', 3: 'subsection', 4: 'subsubsection'}[
                len(heading.group(1))
            ]
            title = re.sub(r'^\d+(?:\.\d+)*\.?\s*', '', heading.group(2))
            output.extend([rf'\{command}{{{inline_markup(title)}}}', ''])
            index += 1
            continue

        if line.lstrip().startswith('|'):
            flush_paragraph()
            close_lists(output, lists)
            rows, index = table_rows(lines, index)
            output.extend(render_table(rows))
            output.append('')
            continue

        bullet = re.match(r'^\s*-\s+(.+)$', line)
        numbered = re.match(r'^\s*\d+\.\s+(.+)$', line)
        if bullet or numbered:
            flush_paragraph()
            environment = 'itemize' if bullet else 'enumerate'
            if not lists or lists[-1] != environment:
                close_lists(output, lists)
                output.append(r'\begin{' + environment + '}')
                lists.append(environment)
            output.append(r'\item ' + inline_markup((bullet or numbered).group(1)))
            index += 1
            continue

        if line.startswith('> '):
            flush_paragraph()
            close_lists(output, lists)
            output.extend([
                r'\begin{quote}',
                inline_markup(line[2:]),
                r'\end{quote}',
                '',
            ])
            index += 1
            continue

        if not line.strip():
            flush_paragraph()
            close_lists(output, lists)
            if output and output[-1] != '':
                output.append('')
            index += 1
            continue

        paragraph.append(line)
        index += 1

    flush_paragraph()
    close_lists(output, lists)
    if in_code:
        output.append(r'\end{MuseCode}')
    return '\n'.join(output)


def main():
    body = convert(SOURCE.read_text(encoding='utf-8'))
    TARGET.write_text(PREAMBLE + body + POSTAMBLE, encoding='utf-8')
    print(TARGET)


if __name__ == '__main__':
    main()
