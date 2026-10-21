"""Generate the README's backend coverage badge from pytest's coverage.xml."""
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    coverage = ET.parse(ROOT / 'coverage.xml').getroot()
    percent = 100 * float(coverage.attrib['line-rate'])
    label = f'{percent:.0f}%'
    color = '#4c1' if percent >= 90 else '#dfb317' if percent >= 70 else '#e05d44'
    output = ROOT / 'docs' / 'coverage.svg'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(f'''<svg xmlns="http://www.w3.org/2000/svg" width="181" height="20" role="img" aria-label="backend coverage: {label}">
  <title>backend coverage: {label}</title>
  <rect width="140" height="20" fill="#555"/>
  <rect x="140" width="41" height="20" fill="{color}"/>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,DejaVu Sans,sans-serif" font-size="11">
    <text x="70" y="14">backend coverage</text>
    <text x="160" y="14">{label}</text>
  </g>
</svg>
''', encoding='utf-8')


if __name__ == '__main__':
    main()
