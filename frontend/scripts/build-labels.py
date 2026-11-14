#!/usr/bin/env python3
"""
Build public/data/labels.geojson from Natural Earth.

Natural Earth is public domain. Run this to add places, change the area of
interest, or refresh the data:

    python3 scripts/build-labels.py

It caches the downloads in .ne-cache/ so a re-run is quick.
"""
import json
import pathlib
import urllib.request
from collections import Counter

BASE = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson'
CACHE = pathlib.Path(__file__).parent.parent / '.ne-cache'
OUT = pathlib.Path(__file__).parent.parent / 'public' / 'data' / 'labels.geojson'

# Everything shown at province level; also decides which countries' towns we keep.
NEIGHBOURS = {'Turkey', 'Greece', 'Bulgaria', 'Georgia', 'Armenia', 'Azerbaijan',
              'Iran', 'Iraq', 'Syria', 'Cyprus', 'Northern Cyprus'}

# Turkish letters English does not use. Folding them is how English-language
# sources write these names: İzmir -> Izmir, Gümüşhane -> Gumushane.
TR_FOLD = str.maketrans({
    'İ': 'I', 'ı': 'i', 'Ğ': 'G', 'ğ': 'g', 'Ş': 'S', 'ş': 's',
    'Ç': 'C', 'ç': 'c', 'Ö': 'O', 'ö': 'o', 'Ü': 'U', 'ü': 'u',
    'Â': 'A', 'â': 'a', 'Î': 'I', 'î': 'i', 'Û': 'U', 'û': 'u',
})
# Typographic punctuation, so no extra glyph range has to be downloaded.
PUNCT = str.maketrans({'–': '-', '—': '-', '‘': "'", '’': "'"})


def english(value: str) -> str:
    return (value or '').translate(TR_FOLD).translate(PUNCT)


def turkish(value: str) -> str:
    return (value or '').translate(PUNCT)


def upper_en(value: str) -> str:
    """Locale-independent uppercase: India -> INDIA, never INDİA."""
    return value.upper()


def upper_tr(value: str) -> str:
    """Turkish uppercase: dotted i stays dotted, dotless i loses its dot."""
    return value.replace('i', 'İ').replace('ı', 'I').upper()


def fetch(name: str) -> dict:
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f'{name}.geojson'
    if not path.exists():
        print(f'  downloading {name} …')
        urllib.request.urlretrieve(f'{BASE}/{name}.geojson', path)
    return json.loads(path.read_text(encoding='utf-8'))


def main() -> None:
    feats: list[dict] = []

    def add(lon, lat, en, tr, kind):
        props = {'en': en, 'k': kind}
        if tr and tr != en:
            props['tr'] = tr
        feats.append({
            'type': 'Feature',
            'properties': props,
            'geometry': {'type': 'Point',
                         'coordinates': [round(float(lon), 4), round(float(lat), 4)]},
        })

    # ------------------------------------------------------------- countries
    # Uppercased here rather than with the style's text-transform, because
    # MapLibre uppercases with the *browser's* locale: on a Turkish system
    # that turns India into INDİA.
    for f in fetch('ne_110m_admin_0_countries')['features']:
        p = f['properties']
        x, y = p.get('LABEL_X'), p.get('LABEL_Y')
        if x is None or y is None:
            continue
        en = english(p.get('NAME_EN') or p.get('NAME_LONG') or p.get('NAME'))
        tr = turkish(p.get('NAME_TR') or en)
        kind = 'country_major' if (p.get('LABELRANK') or 9) <= 3 else 'country'
        add(x, y, upper_en(en), upper_tr(tr), kind)

    # ------------------------------------------------------------- provinces
    # Names from the full dataset (the label-point file has only one, badly
    # mixed, name field); coordinates from the label points, joined by code.
    names: dict[str, tuple[str, str]] = {}
    for f in fetch('ne_10m_admin_1_states_provinces')['features']:
        p = f['properties']
        if p.get('admin') not in NEIGHBOURS:
            continue
        code = p.get('adm1_code')
        base = p.get('name_en') or p.get('name') or ''
        if code and base:
            names[code] = (english(base), turkish(p.get('name_tr') or base))

    seen: set[str] = set()
    points = fetch('ne_10m_admin_1_label_points')['features']
    for f in sorted(points, key=lambda f: f['properties'].get('scalerank', 99)):
        code = f['properties'].get('adm1_code')
        if code not in names or code in seen:
            continue
        seen.add(code)
        lon, lat = f['geometry']['coordinates'][:2]
        add(lon, lat, *names[code], 'region')

    # ----------------------------------------------------------- settlements
    for f in fetch('ne_10m_populated_places')['features']:
        p = f['properties']
        rank = p.get('SCALERANK', 10)
        local = p.get('ADM0NAME') in NEIGHBOURS
        if not local and rank > 2:   # worldwide, only the biggest
            continue
        # NAMEASCII is Natural Earth's own romanisation; fold what it misses.
        en = english(p.get('NAMEASCII') or p.get('NAME_EN') or p.get('NAME') or '')
        if not en:
            continue
        tr = turkish(p.get('NAME_TR') or p.get('NAME') or en)
        lon, lat = f['geometry']['coordinates'][:2]
        kind = 'capital' if p.get('ADM0CAP') == 1 else ('city' if rank <= 5 else 'town')
        add(lon, lat, en, tr, kind)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({'type': 'FeatureCollection', 'features': feats},
                              ensure_ascii=False, separators=(',', ':')),
                   encoding='utf-8')

    print(f'\n{len(feats)} labels', dict(Counter(f['properties']['k'] for f in feats)))
    print(f'{sum(1 for f in feats if "tr" in f["properties"])} have a distinct Turkish name')
    print(f'{OUT.stat().st_size // 1024} KB -> {OUT}')

    # The app ships glyphs for these ranges only.
    over = {ch for f in feats for k in ('en', 'tr') for ch in f['properties'].get(k, '')
            if ord(ch) > 767}
    if over:
        print(f'WARNING: characters beyond the bundled glyph ranges: {sorted(over)}')


if __name__ == '__main__':
    main()
