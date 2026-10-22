/**
 * Minimal, dependency-free PLY reader for the point clouds produced by roomscape-recon
 * (binary_little_endian or ascii, vertex x/y/z + optional red/green/blue).
 * Pure function: unit-tested in tests/unit/ply.test.ts.
 */

export type PointCloud = { count: number; positions: Float32Array; colors: Float32Array | null };

const SIZES: Record<string, number> = {
  char: 1, uchar: 1, int8: 1, uint8: 1,
  short: 2, ushort: 2, int16: 2, uint16: 2,
  int: 4, uint: 4, int32: 4, uint32: 4, float: 4, float32: 4,
  double: 8, float64: 8,
};

function read(view: DataView, off: number, type: string, le: boolean): number {
  switch (type) {
    case "char": case "int8": return view.getInt8(off);
    case "uchar": case "uint8": return view.getUint8(off);
    case "short": case "int16": return view.getInt16(off, le);
    case "ushort": case "uint16": return view.getUint16(off, le);
    case "int": case "int32": return view.getInt32(off, le);
    case "uint": case "uint32": return view.getUint32(off, le);
    case "float": case "float32": return view.getFloat32(off, le);
    case "double": case "float64": return view.getFloat64(off, le);
    default: throw new Error(`Unsupported PLY type ${type}`);
  }
}

export function parsePly(buffer: ArrayBuffer): PointCloud {
  const bytes = new Uint8Array(buffer);
  const marker = "end_header";
  let headerEnd = -1;
  const limit = Math.min(bytes.length, 64 * 1024);
  let headerText = "";
  for (let i = 0; i < limit; i++) headerText += String.fromCharCode(bytes[i]);
  const idx = headerText.indexOf(marker);
  if (!headerText.startsWith("ply") || idx < 0) throw new Error("Not a PLY file");
  headerEnd = idx + marker.length;
  // skip \r?\n after end_header
  if (headerText[headerEnd] === "\r") headerEnd++;
  if (headerText[headerEnd] === "\n") headerEnd++;
  const lines = headerText.slice(0, idx).split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  let format = "";
  let count = 0;
  let inVertex = false;
  const props: { name: string; type: string }[] = [];
  const otherElementsBefore: number[] = [];
  for (const line of lines) {
    const parts = line.split(/\s+/);
    if (parts[0] === "format") format = parts[1];
    else if (parts[0] === "element") {
      inVertex = parts[1] === "vertex";
      if (inVertex) count = parseInt(parts[2], 10);
      else if (count === 0) otherElementsBefore.push(parseInt(parts[2], 10));
    } else if (parts[0] === "property" && inVertex) {
      if (parts[1] === "list") throw new Error("List properties on vertices are not supported");
      props.push({ type: parts[1], name: parts[2] });
    }
  }
  if (otherElementsBefore.some((n) => n > 0)) throw new Error("Vertex element must come first");
  if (!Number.isFinite(count) || count < 0) throw new Error("Invalid vertex count");
  const ix = props.findIndex((p) => p.name === "x");
  const iy = props.findIndex((p) => p.name === "y");
  const iz = props.findIndex((p) => p.name === "z");
  if (ix < 0 || iy < 0 || iz < 0) throw new Error("PLY has no x/y/z");
  const ir = props.findIndex((p) => p.name === "red" || p.name === "r");
  const ig = props.findIndex((p) => p.name === "green" || p.name === "g");
  const ib = props.findIndex((p) => p.name === "blue" || p.name === "b");
  const hasColor = ir >= 0 && ig >= 0 && ib >= 0;
  const positions = new Float32Array(count * 3);
  const colors = hasColor ? new Float32Array(count * 3) : null;
  const colorScale = (i: number) => (SIZES[props[i].type] === 1 ? 1 / 255 : props[i].type.startsWith("float") ? 1 : 1 / 65535);

  if (format === "ascii") {
    const body = new TextDecoder().decode(bytes.subarray(headerEnd)).split(/\r?\n/);
    for (let v = 0, li = 0; v < count && li < body.length; li++) {
      const t = body[li].trim();
      if (!t) continue;
      const vals = t.split(/\s+/).map(Number);
      positions.set([vals[ix], vals[iy], vals[iz]], v * 3);
      if (colors) colors.set([vals[ir] * colorScale(ir), vals[ig] * colorScale(ig), vals[ib] * colorScale(ib)], v * 3);
      v++;
    }
    return { count, positions, colors };
  }
  const le = format === "binary_little_endian";
  if (!le && format !== "binary_big_endian") throw new Error(`Unknown PLY format ${format}`);
  const offsets: number[] = [];
  let stride = 0;
  for (const p of props) {
    const s = SIZES[p.type];
    if (!s) throw new Error(`Unsupported PLY type ${p.type}`);
    offsets.push(stride);
    stride += s;
  }
  if (headerEnd + stride * count > bytes.length) throw new Error("PLY is truncated");
  const view = new DataView(buffer, headerEnd);
  for (let v = 0; v < count; v++) {
    const base = v * stride;
    positions[v * 3] = read(view, base + offsets[ix], props[ix].type, le);
    positions[v * 3 + 1] = read(view, base + offsets[iy], props[iy].type, le);
    positions[v * 3 + 2] = read(view, base + offsets[iz], props[iz].type, le);
    if (colors) {
      colors[v * 3] = read(view, base + offsets[ir], props[ir].type, le) * colorScale(ir);
      colors[v * 3 + 1] = read(view, base + offsets[ig], props[ig].type, le) * colorScale(ig);
      colors[v * 3 + 2] = read(view, base + offsets[ib], props[ib].type, le) * colorScale(ib);
    }
  }
  return { count, positions, colors };
}
