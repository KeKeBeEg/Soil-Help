/**
 * A small GeoTIFF reader for the backend's result files: classic TIFF,
 * deflate or no compression, strips or tiles, one or many bands, 8/16/32-bit
 * integers or 32/64-bit floats. Enough for what rasterio writes for us, with
 * no dependency: inflating uses the browser's own DecompressionStream.
 */

export interface GeoRaster {
  width: number;
  height: number;
  /** One array per band, row by row from the top. */
  bands: Float32Array[];
  /** Band names (GDAL band descriptions), e.g. the years of a *_by_year file. */
  bandNames: string[];
  nodata: number | null;
  /** EPSG code of the coordinate system, e.g. 32617 for UTM zone 17N. */
  epsg: number;
  /** Top-left corner and pixel size, in the file's coordinates. */
  originX: number;
  originY: number;
  pixelX: number;
  pixelY: number;
  /** GDAL metadata items (DESCRIPTION, UNITS, CLASS_0, ...). */
  meta: Record<string, string>;
}

const TYPE_SIZE: Record<number, number> = { 1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 16: 8 };

type TagValue = number[] | string;

function readTags(view: DataView, le: boolean): Map<number, TagValue> {
  if (view.getUint16(2, le) !== 42) throw new Error('not a classic TIFF (BigTIFF is not supported)');
  const ifd = view.getUint32(4, le);
  const count = view.getUint16(ifd, le);
  const tags = new Map<number, TagValue>();
  for (let i = 0; i < count; i++) {
    const at = ifd + 2 + i * 12;
    const tag = view.getUint16(at, le);
    const type = view.getUint16(at + 2, le);
    const n = view.getUint32(at + 4, le);
    const size = (TYPE_SIZE[type] ?? 1) * n;
    const off = size <= 4 ? at + 8 : view.getUint32(at + 8, le);
    if (type === 2) {
      let s = '';
      for (let k = 0; k < n; k++) {
        const c = view.getUint8(off + k);
        if (c) s += String.fromCharCode(c);
      }
      tags.set(tag, s);
      continue;
    }
    const values: number[] = [];
    for (let k = 0; k < n; k++) {
      switch (type) {
        case 1: case 7: values.push(view.getUint8(off + k)); break;
        case 6: values.push(view.getInt8(off + k)); break;
        case 3: values.push(view.getUint16(off + 2 * k, le)); break;
        case 8: values.push(view.getInt16(off + 2 * k, le)); break;
        case 4: values.push(view.getUint32(off + 4 * k, le)); break;
        case 9: values.push(view.getInt32(off + 4 * k, le)); break;
        case 11: values.push(view.getFloat32(off + 4 * k, le)); break;
        case 12: values.push(view.getFloat64(off + 8 * k, le)); break;
        case 5: values.push(view.getUint32(off + 8 * k, le) / view.getUint32(off + 8 * k + 4, le)); break;
        default: values.push(0);
      }
    }
    tags.set(tag, values);
  }
  return tags;
}

async function inflate(bytes: Uint8Array): Promise<Uint8Array> {
  // TIFF deflate is zlib-wrapped, which is what the 'deflate' format means here.
  const stream = new Blob([bytes as BlobPart]).stream().pipeThrough(new DecompressionStream('deflate'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/** GDAL keeps band names and dataset tags as XML in tag 42112. */
function parseGdalMeta(xml: string): { meta: Record<string, string>; bandNames: string[] } {
  const meta: Record<string, string> = {};
  const bandNames: string[] = [];
  const re = /<Item\s+name="([^"]*)"([^>]*)>([\s\S]*?)<\/Item>/g;
  const unescape = (s: string) =>
    s.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, '&');
  for (let m; (m = re.exec(xml)); ) {
    const sample = /sample="(\d+)"/.exec(m[2]);
    const role = /role="([^"]*)"/.exec(m[2]);
    if (sample) {
      if (role?.[1] === 'description') bandNames[Number(sample[1])] = unescape(m[3]);
    } else {
      meta[m[1]] = unescape(m[3]);
    }
  }
  return { meta, bandNames };
}

function epsgOf(geoKeys: number[] | undefined): number {
  if (!geoKeys) return 0;
  const n = geoKeys[3];
  let geographic = 0;
  for (let i = 0; i < n; i++) {
    const [key, loc, , value] = geoKeys.slice(4 + i * 4, 8 + i * 4);
    if (loc !== 0) continue;
    if (key === 3072) return value; // ProjectedCSTypeGeoKey
    if (key === 2048) geographic = value; // GeographicTypeGeoKey
  }
  return geographic;
}

export async function parseGeoTiff(buffer: ArrayBuffer): Promise<GeoRaster> {
  const view = new DataView(buffer);
  const order = view.getUint16(0, false);
  if (order !== 0x4949 && order !== 0x4d4d) throw new Error('not a TIFF file');
  const le = order === 0x4949;
  const tags = readTags(view, le);
  const num = (tag: number, fallback = 0) => {
    const v = tags.get(tag);
    return Array.isArray(v) && v.length ? v[0] : fallback;
  };
  const arr = (tag: number) => {
    const v = tags.get(tag);
    return Array.isArray(v) ? v : undefined;
  };

  const width = num(256);
  const height = num(257);
  const spp = num(277, 1);
  const bits = num(258, 8);
  const format = num(339, 1); // 1 unsigned, 2 signed, 3 float
  const compression = num(259, 1);
  const predictor = num(317, 1);
  const planar = num(284, 1);
  if (compression !== 1 && compression !== 8 && compression !== 32946) {
    throw new Error(`unsupported TIFF compression ${compression}`);
  }

  const tiled = tags.has(322);
  const blockW = tiled ? num(322) : width;
  const blockH = tiled ? num(323) : num(278, height);
  const offsets = arr(tiled ? 324 : 273) ?? [];
  const counts = arr(tiled ? 325 : 279) ?? [];
  const across = Math.ceil(width / blockW);
  const down = Math.ceil(height / blockH);
  const perBand = across * down;
  const bytes = bits / 8;
  const samplesInBlock = planar === 2 ? 1 : spp;

  const bands = Array.from({ length: spp }, () => new Float32Array(width * height));
  const raw = new Uint8Array(buffer);

  await Promise.all(
    offsets.map(async (offset, index) => {
      let block: Uint8Array = raw.subarray(offset, offset + counts[index]);
      if (compression !== 1) block = await inflate(block);
      const band0 = planar === 2 ? Math.floor(index / perBand) : 0;
      const local = planar === 2 ? index % perBand : index;
      const bx = (local % across) * blockW;
      const by = Math.floor(local / across) * blockH;
      const rowBytes = blockW * samplesInBlock * bytes;
      const rows = Math.min(blockH, Math.floor(block.length / rowBytes));

      if (predictor === 3) {
        // Floating-point predictor: undo the byte differencing, then the byte shuffle.
        const out = new Uint8Array(block.length);
        for (let r = 0; r < rows; r++) {
          const row = block.slice(r * rowBytes, (r + 1) * rowBytes);
          for (let i = samplesInBlock; i < row.length; i++) row[i] = (row[i] + row[i - samplesInBlock]) & 0xff;
          const n = blockW * samplesInBlock;
          for (let i = 0; i < n; i++) {
            for (let b = 0; b < bytes; b++) {
              // bytes were stored most significant first
              out[r * rowBytes + i * bytes + (le ? bytes - 1 - b : b)] = row[b * n + i];
            }
          }
        }
        block = out;
      }

      const dv = new DataView(block.buffer, block.byteOffset, block.byteLength);
      for (let r = 0; r < rows; r++) {
        const y = by + r;
        if (y >= height) break;
        const prev = new Array<number>(samplesInBlock).fill(0);
        for (let c = 0; c < blockW; c++) {
          const x = bx + c;
          for (let s = 0; s < samplesInBlock; s++) {
            const at = ((r * blockW + c) * samplesInBlock + s) * bytes;
            let v: number;
            if (format === 3) v = bytes === 4 ? dv.getFloat32(at, le) : dv.getFloat64(at, le);
            else if (bytes === 1) v = format === 2 ? dv.getInt8(at) : dv.getUint8(at);
            else if (bytes === 2) v = format === 2 ? dv.getInt16(at, le) : dv.getUint16(at, le);
            else v = format === 2 ? dv.getInt32(at, le) : dv.getUint32(at, le);
            if (predictor === 2) {
              v += prev[s];
              prev[s] = v; // horizontal differencing, integer data only
            }
            if (x < width) bands[planar === 2 ? band0 : s][y * width + x] = v;
          }
        }
      }
    }),
  );

  const scale = arr(33550) ?? [1, 1];
  const tie = arr(33922) ?? [0, 0, 0, 0, 0, 0];
  const nodataText = tags.get(42113);
  const nodata = typeof nodataText === 'string' && nodataText.trim() !== '' ? Number(nodataText.trim()) : null;
  const gdal = typeof tags.get(42112) === 'string' ? parseGdalMeta(tags.get(42112) as string) : { meta: {}, bandNames: [] };

  return {
    width,
    height,
    bands,
    bandNames: Array.from({ length: spp }, (_, i) => gdal.bandNames[i] ?? String(i + 1)),
    nodata,
    epsg: epsgOf(arr(34735)),
    originX: tie[3] - tie[0] * scale[0],
    originY: tie[4] + tie[1] * scale[1],
    pixelX: scale[0],
    pixelY: scale[1],
    meta: gdal.meta,
  };
}

/** True where a cell holds no data. */
export function isNoData(v: number, nodata: number | null): boolean {
  return Number.isNaN(v) || (nodata !== null && !Number.isNaN(nodata) && v === nodata);
}
