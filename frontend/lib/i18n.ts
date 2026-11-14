export type Lang = 'tr' | 'en';

/**
 * Plain-language strings only. Nothing here should read like a formula:
 * farmers see "soil wetness", never "W"; "risk", never "P(FS<1)".
 */
const dict = {
  appName: { tr: 'Yamaç', en: 'Yamaç' },
  tagline: { tr: 'Tarla Görüntüleyici', en: 'Field Viewer' },

  // Panel sections
  map: { tr: 'Harita', en: 'Map' },
  goTo: { tr: 'Git', en: 'Go to' },
  goToPlaceholder: {
    tr: 'Köy, ilçe, adres veya enlem, boylam',
    en: 'Town, district, address, or lat, long',
  },
  go: { tr: 'Git', en: 'Go' },
  searching: { tr: 'Aranıyor…', en: 'Searching…' },
  noResults: { tr: 'Sonuç bulunamadı', en: 'No results found' },
  searchFailed: { tr: 'Arama başarısız oldu', en: 'Search failed' },

  rendering: { tr: 'Görünüm', en: 'Rendering' },
  satellite: { tr: 'Uydu', en: 'Satellite' },
  hybrid: { tr: 'Uydu + isimler', en: 'Hybrid' },
  relief: { tr: 'Kabartma (gri)', en: 'Shaded relief (gray)' },
  topo: { tr: 'Topoğrafik', en: 'Topographic' },

  reliefHeight: { tr: 'Kabartma yüksekliği', en: 'Relief height' },
  off: { tr: 'Kapalı', en: 'Off' },

  reliefShadows: { tr: 'Kabartma ve gölgeler', en: 'Relief & shadows' },
  sunDirection: { tr: 'Güneş yönü', en: 'Sun direction' },
  shadowStrength: { tr: 'Gölge koyuluğu', en: 'Shadow strength' },
  blendIntoImagery: { tr: 'Uyduyla harmanla', en: 'Blend into imagery' },

  tools: { tr: 'Araçlar', en: 'Tools' },
  drawProfile: { tr: 'Yükseklik profili çiz', en: 'Draw elevation profile' },
  drawField: { tr: 'Tarla sınırı çiz', en: 'Draw field area' },

  layers: { tr: 'Katmanlar', en: 'Layers' },
  satelliteImagery: { tr: 'Uydu görüntüsü', en: 'Satellite imagery' },
  placeLabels: { tr: 'Yer isimleri', en: 'Place labels' },
  roads: { tr: 'Yollar', en: 'Roads' },
  hillshade: { tr: 'Kabartma gölgesi', en: 'Hillshade' },
  myFields: { tr: 'Çizdiğim tarlalar', en: 'My drawn fields' },

  comingSoon: { tr: 'yakında', en: 'coming soon' },
  riskLayers: { tr: 'Risk katmanları', en: 'Risk layers' },
  risk: { tr: 'Heyelan riski', en: 'Landslide risk' },
  wetness: { tr: 'Toprak nemi', en: 'Soil wetness' },
  soilLoss: { tr: 'Toprak kaybı', en: 'Soil loss' },
  regional: { tr: 'Bölgesel uyarı', en: 'Regional watch' },
  riskNote: {
    tr: 'Bu katmanlar model çıktısı bağlandığında açılacak.',
    en: 'These switch on once the model output is connected.',
  },

  // Drawing
  points: { tr: 'nokta', en: 'points' },
  finishHint: {
    tr: 'bitirmek için çift tıklayın veya Bitti',
    en: 'double-click or Done to finish',
  },
  firstPointHint: {
    tr: 'Başlamak için haritaya tıklayın',
    en: 'Click the map to start',
  },
  undo: { tr: 'Geri al', en: 'Undo' },
  clear: { tr: 'Temizle', en: 'Clear' },
  done: { tr: 'Bitti', en: 'Done' },
  cancel: { tr: 'İptal', en: 'Cancel' },
  area: { tr: 'Alan', en: 'Area' },
  perimeter: { tr: 'Çevre', en: 'Perimeter' },
  length: { tr: 'Uzunluk', en: 'Length' },

  // Profile
  elevationProfile: { tr: 'Yükseklik profili', en: 'Elevation profile' },
  readingElevation: { tr: 'Yükseklik okunuyor…', en: 'Reading elevation along the line…' },
  elevationFailed: {
    tr: 'Yükseklik verisi alınamadı. Bağlantınızı kontrol edin.',
    en: 'Could not read elevation data. Check your connection.',
  },
  addPoints: { tr: 'Nokta ekle', en: 'Add points' },
  saveCsv: { tr: 'CSV kaydet', en: 'Save CSV' },
  close: { tr: 'Kapat', en: 'Close' },
  minMax: { tr: 'En düşük / en yüksek', en: 'Min / max' },
  gain: { tr: 'Toplam tırmanış', en: 'Total climb' },
  maxSlope: { tr: 'En dik eğim', en: 'Steepest slope' },

  // Controls
  zoomIn: { tr: 'Yakınlaştır', en: 'Zoom in' },
  zoomOut: { tr: 'Uzaklaştır', en: 'Zoom out' },
  resetNorth: { tr: 'Kuzeyi sıfırla', en: 'Reset north' },
  myLocation: { tr: 'Konumum', en: 'My location' },
  locationDenied: { tr: 'Konum izni verilmedi', en: 'Location permission denied' },
  tilt: { tr: 'Eğ', en: 'Tilt' },
  view2d: { tr: '2B', en: '2D' },
  view3d: { tr: '3B', en: '3D' },
  liteMode: { tr: 'Hafif mod', en: 'Lite mode' },
  liteModeOn: {
    tr: 'Hafif mod açık: düz harita, hızlı yükleme.',
    en: 'Lite mode on: flat map, faster loading.',
  },
  collapse: { tr: 'Paneli kapat', en: 'Collapse panel' },
  expand: { tr: 'Paneli aç', en: 'Open panel' },
  language: { tr: 'Dil', en: 'Language' },

  // Misc
  elevation: { tr: 'Yükseklik', en: 'Elevation' },
  loading: { tr: 'Yükleniyor…', en: 'Loading…' },

  // Top bar menus
  menuFile: { tr: 'Dosya', en: 'File' },
  menuView: { tr: 'Görünüm', en: 'View' },
  menuTools: { tr: 'Araçlar', en: 'Tools' },
  menuHelp: { tr: 'Yardım', en: 'Help' },
  exportGeoJson: { tr: 'Çizimleri GeoJSON olarak indir', en: 'Export drawings as GeoJSON' },
  exportProfileCsv: { tr: 'Profili CSV olarak indir', en: 'Export profile as CSV' },
  clearAll: { tr: 'Tümünü temizle', en: 'Clear everything' },
  about: { tr: 'Hakkında ve kaynaklar', en: 'About & data sources' },
  shortcuts: { tr: 'Kısayollar', en: 'Keyboard shortcuts' },

  // Map content panel
  mapContent: { tr: 'Harita içeriği', en: 'Map content' },
  mapStyle: { tr: 'Harita stili', en: 'Map style' },
  emptyContent: {
    tr: 'Henüz bir şey çizmediniz. Yukarıdaki araçlarla işaret koyun veya tarla çizin.',
    en: 'Nothing drawn yet. Use the tools above to drop a pin or outline a field.',
  },
  rename: { tr: 'Yeniden adlandır', en: 'Rename' },
  remove: { tr: 'Sil', en: 'Remove' },
  flyHere: { tr: 'Buraya git', en: 'Fly here' },
  show: { tr: 'Göster', en: 'Show' },
  hide: { tr: 'Gizle', en: 'Hide' },

  // Tools
  dropPin: { tr: 'İşaret koy', en: 'Drop a pin' },
  pinHint: { tr: 'İşaret koymak için haritaya tıklayın', en: 'Click the map to drop a pin' },
  redo: { tr: 'İleri al', en: 'Redo' },
  toggleSidebar: { tr: 'Kenar panelini aç/kapat', en: 'Toggle sidebar' },

  // Status bar
  camera: { tr: 'Kamera', en: 'Camera' },
  zoomLevel: { tr: 'Yakınlık', en: 'Zoom' },
  dataSources: { tr: 'Veri kaynakları', en: 'Data sources' },
  notOfficialWarning: {
    tr: 'Bu bir resmi uyarı değildir. Acil durumlarda AFAD’ı arayın.',
    en: 'This is not an official warning. In an emergency contact AFAD.',
  },
  closeDialog: { tr: 'Kapat', en: 'Close' },
  demError: {
    tr: 'Arazi verisi yüklenemedi; harita düz gösteriliyor.',
    en: 'Terrain data did not load; showing a flat map.',
  },
} as const;

export type StringKey = keyof typeof dict;

export function t(key: StringKey, lang: Lang): string {
  return dict[key][lang];
}

/** Bound translator, so components can write `tr('layers')`. */
export function translator(lang: Lang) {
  return (key: StringKey) => dict[key][lang];
}
