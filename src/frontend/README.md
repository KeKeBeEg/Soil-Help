# Soil Help — Field Viewer (frontend)

A Google Earth–style 3D satellite and terrain map for farm fields.
Next.js + React + MapLibre GL, no API keys.

This is the **map shell**. The landslide-risk, wetness and soil-loss layers
from the Python model are not wired up yet; they appear in the sidebar marked
"coming soon".

## Running it

Requires Node.js 20 or newer.

```bash
npm install
npm run dev          # http://localhost:3000
```

Production build:

```bash
npm run build
npm start
```

On a phone on the same Wi-Fi: `npm run dev -- -H 0.0.0.0`, then open
`http://<your-computer-ip>:3000`. "My location" needs https, so that one
button will not work over a plain LAN address.

## The interface

```
┌─────────────────────────────────────────────────────────┐
│ Soil Help  File View Tools Help              TR EN  (i) │  menu bar
├─────────────────────────────────────────────────────────┤
│ ▣ │ 🔍 search      │ ↶ ↷ │ 📍 〰 ⬟ │ ▤ ☀               │  toolbar
├───────────────┬─────────────────────────────────────────┤
│ Map content   │                                         │
│  📍 Pin 1     │              3D map                ┌──┐ │
│  ⬟ Field 1    │                                    │◉ │ │
│  〰 Profile 1  │                                   │3D│ │
│               │                                    │ +│ │
│ Map style     │  ┌────────┐                        │ −│ │
│ Relief & …    │  │ inset  │                        └──┘ │
│               ├─────────────────────────────────────────┤
│               │  Elevation profile              chart   │
├───────────────┴─────────────────────────────────────────┤
│ credits        Imperial|Metric  ──── 2 km  Zoom  Camera  │  status bar
└─────────────────────────────────────────────────────────┘
```

- **Globe** that flies in to the field on load; satellite imagery over real
  terrain. Right-drag or two fingers to tilt and rotate.
- **Tools**: drop a pin, draw a field outline (live area and perimeter), draw
  an elevation profile (chart with hover crosshair, CSV export).
- **Map content** lists everything drawn, with rename, show/hide, fly-to and
  delete. File → export it all as GeoJSON.
- **Rendering**: satellite, hybrid, shaded relief, topographic; relief height,
  sun direction and shadow strength.
- **Turkish and English**, switchable in the menu bar.
- **Lite mode** — flat 2D, no terrain — automatic on weak devices, slow
  connections and Data Saver, and toggleable by hand.
- **A starfield behind the globe**, drawn once into a canvas at device pixel
  ratio. Space in the map itself is transparent so it shows through.
- **Opens on the whole Earth**, deliberately nowhere in particular — the user
  arrives by searching or by letting the browser locate them. The resting
  camera is `HOME_VIEW` in `lib/config.ts`.
- **The loading screen is the globe itself.** The map starts at `INTRO_VIEW`
  with the interface folded away, so the first thing on screen is the Earth
  on the starfield with the wordmark over it. Once the tiles have actually
  drawn, the globe spins to `HOME_VIEW` over `INTRO_SPIN_MS` and the panels
  unfold around it. No progress bar, no second asset to load.

### Tuning the opening

Everything about it is three constants in `lib/config.ts`:

| Constant | What it does |
|---|---|
| `INTRO_VIEW` | where the globe sits while the wordmark is up |
| `HOME_VIEW` | where it comes to rest, and where the app lives afterwards |
| `INTRO_SPIN_MS` | how long the spin takes |

`components/Splash.tsx` is only a veil and the wordmark — there is no image to
replace. The choreography that unfolds the interface is CSS: the delays on
`.app` / `.sidebar` / `.topbar` in `app/globals.css` are timed against
`INTRO_SPIN_MS`, so change both together. Under
`prefers-reduced-motion: reduce`, and in lite mode, the camera jumps instead
of spinning.

## Notes for whoever works on this next

**Telemetry stays out of React.** The map fires `move` on every frame. Pushing
that through React state re-rendered the whole screen 60 times a second and
made fast zooming stutter. `lib/telemetry.ts` is a small external store;
only the status bar, compass and inset subscribe to it.

**Terrain is off above `TERRAIN_MIN_ZOOM`.** At world scale a 30 m DEM buys
nothing visible and still costs a screenful of tiles — which is exactly what
the opening spin was doing. The terrain effect checks the zoom and only calls
`setTerrain` when it is worth it; hillshade carries a `minzoom` for the same
reason.

**Camera limits live in `lib/config.ts`.** `MIN_ZOOM` stops the view at a whole
Earth, and the pitch is eased to flat below `PITCH_FREE_ZOOM`; without that,
tilting a world-scale map points the camera at empty space. The globe and the
flat projection need different floors — `MIN_ZOOM` and `MIN_ZOOM_FLAT` — because
Mercator shows black bands above and below the map where the globe shows space.

**Place names are drawn by the map, not fetched as pictures.** Every keyless
raster label service has now gone behind an API key, and a raster label tile
is soft on a high-DPI screen anyway. `public/data/labels.geojson` is a 110 KB
Natural Earth extract (countries, provinces around the study area, and
settlements) rendered as MapLibre symbol layers, with the font glyphs
self-hosted in `public/glyphs`. Sharp at any zoom and pixel ratio, no key, no
third-party request, and nothing that can be withdrawn.

`scripts/build-labels.py` regenerates the file from Natural Earth — run it to
add places or change the area covered.

Names are bilingual. Each feature carries `en`, plus `tr` only where the two
differ (Türkiye / Turkey, Gümüşhane / Gumushane), and `k` — one of
`country_major`, `country`, `region`, `capital`, `city`, `town` — which sets
the zoom band and text size. The map switches `text-field` between the two
when the language changes.

The English forms are romanised: Turkish has letters English does not use, so
`İ ı ğ ş ç ö ü` fold to `I i g s c o u`, which is how English-language sources
write these names. Natural Earth's own `NAME_EN` keeps Turkish spelling for
Turkish places, so folding is applied on top of it when the data is built.

Country names are stored **already uppercased**, and the style does no
`text-transform`. MapLibre uppercases with `toLocaleUpperCase()`, which follows
the *browser's* locale — on a Turkish system that turns India into INDİA, in
either language. The build script uppercases each name with its own language's
rules instead: INDIA and ETHIOPIA in English, HİNDİSTAN and MISIR in Turkish.

**Effects that touch the map depend on `styleReady`.** They run once on mount,
before the style has loaded, and would otherwise bail out and never run again.

**The UI font is vendored, not a dependency.** Two Inter `.woff2` files sit in
`public/fonts` with `@font-face` rules at the top of `globals.css`. No npm
package, no Google Fonts request, nothing to install — and `unicode-range`
means a Turkish or English page downloads only the two Latin subsets.

**The palette is deliberately restrained.** Neutral Google-style surfaces that
separate by value rather than by borders, sentence-case labels, and a single
blue accent used only for selection and active state. If you add UI, match
that; scattered colour and uppercase tracked headings are what made the first
version look generated.

## Layout

```
app/
  page.tsx                 screen state
  layout.tsx, globals.css  shell and design tokens
  api/geocode/             place search, proxied to Nominatim
  api/dem/[z]/[x]/[y]/     elevation-tile proxy (only if CORS fails)
components/
  MapCanvas.tsx            everything that touches MapLibre
  TopBar.tsx               menu bar
  Toolbar.tsx              search and tools
  Sidebar.tsx              map content and map style
  StatusBar.tsx            scale, zoom, camera height, coordinates
  MiniMap.tsx              world inset
  Starfield.tsx            the night sky behind the globe
  MapControls.tsx          locate, lite, 3D, compass, zoom
  DrawBar.tsx              drawing toolbar
  ProfilePanel.tsx         elevation profile chart
  AboutDialog.tsx          credits
  ui.tsx, Icons.tsx        shared primitives
lib/
  config.ts                tile URLs, camera limits, attribution
  mapStyle.ts              the MapLibre style
  telemetry.ts             out-of-React camera state
  items.ts                 pins, fields and profiles
  elevation.ts             reads elevation from Terrarium DEM tiles
  geo.ts                   area, distance, formatting
  i18n.ts                  Turkish and English strings
```

## If the terrain does not load

The elevation tiles normally send the CORS headers the browser needs. If a
network strips them, the 3D terrain stays flat and the profile reports an
error. Copy `.env.example` to `.env.local`, set `NEXT_PUBLIC_DEM_PROXY=1` and
restart: the tiles then come through this app's own `/api/dem` route.

## Credits

- Imagery © Esri, Maxar, Earthstar Geographics
- Place names: Natural Earth (public domain)
- Label glyphs: Open Sans (Apache 2.0), built by the MapLibre project
- Elevation: Terrarium tiles, AWS Open Data
- FABDEM is produced using Copernicus WorldDEM-30 © DLR e.V.
  FABDEM is CC BY-NC-SA 4.0 — **non-commercial use only**.
- Place search: OpenStreetMap Nominatim

Check each provider's terms before any public deployment.
