// 世界地図の形を docs/world.json に書き出す（地図を変えたいときだけ使う）。
// 使い方: npm install world-atlas@2.0.2 topojson-client@3.1.0 d3-geo@3.1.1 && node scripts/make_world_map.mjs docs/world.json
import { readFileSync, writeFileSync } from "fs";
import { feature, mesh } from "topojson-client";
import { geoNaturalEarth1, geoPath, geoGraticule10 } from "d3-geo";
const topo = JSON.parse(readFileSync("node_modules/world-atlas/countries-110m.json"));
const land = feature(topo, topo.objects.land);
const borders = mesh(topo, topo.objects.countries, (a, b) => a !== b);
const W = 1000, H = 500;
const proj = geoNaturalEarth1().fitExtent([[4, 4], [W - 4, H - 4]], { type: "Sphere" });
const path = geoPath(proj).digits(1);
const out = { width: W, height: H, land: path(land), borders: path(borders), graticule: path(geoGraticule10()), sphere: path({ type: "Sphere" }) };
// 地域の目印の位置（経度, 緯度）
const regions = { us: [-98, 39], latam: [-60, -12], europe: [10, 50], russia: [37, 54], mideast: [45, 29], africa: [20, 5], china: [110, 34], asia: [100, 13], japan: [139.7, 35.7] };
out.points = Object.fromEntries(Object.entries(regions).map(([k, ll]) => [k, proj(ll).map(v => Math.round(v))]));
writeFileSync(process.argv[2], JSON.stringify(out));
console.log(Object.fromEntries(Object.entries(out).map(([k, v]) => [k, typeof v === "string" ? v.length : v])));
