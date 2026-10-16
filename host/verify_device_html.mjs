// 从 device.html 里抠出 buildBMP / crc32 的真实源码来验证，
// 避免"测的代码"和"页面里的代码"不是同一份。
//
// 验证两点：
//   1. 抠出来的 buildBMP 造出的 BMP 和 Python (make_bmp.py) 造的逐字节一致
//   2. .act 解析出来的色板和 palette_qual.csv 一致

import { readFileSync } from 'node:fs';

const HTML = 'C:/FUCKWEB/quick-shake-pic/device.html';
const html = readFileSync(HTML, 'utf8');

function extract(name) {
    // 匹配 `function name(...) { ... }`（按大括号配对）
    const start = html.indexOf(`function ${name}(`);
    if (start < 0) throw new Error('找不到函数 ' + name);
    let i = html.indexOf('{', start);
    let depth = 0;
    for (let j = i; j < html.length; j++) {
        if (html[j] === '{') depth++;
        else if (html[j] === '}') {
            depth--;
            if (depth === 0) return html.slice(start, j + 1);
        }
    }
    throw new Error('大括号不配对: ' + name);
}

const src = ['buildBMP', 'crc32'].map(extract).join('\n');
// crc32 依赖 CRC_TABLE，把它也抠出来
const tableSrc = (() => {
    const s = html.indexOf('const CRC_TABLE');
    let i = html.indexOf('})();', s);
    return html.slice(s, i + 5);
})();

const factory = new Function(tableSrc + '\n' + src + '\nreturn { buildBMP, crc32 };');
const { buildBMP, crc32 } = factory();

// ---------------------------------------------------------------- 色板
const act = new Uint8Array(readFileSync('C:/FUCKWEB/quick-shake-pic/act/48-color-qual.act'));
console.log('48-color-qual.act:', act.length, '字节 ->', act.length / 3, '色');

const csv = readFileSync('C:/FUCKEDA/e6-48color/docs/palette_qual.csv', 'utf8').trim().split('\n').slice(1);
const csvColors = csv.map((l) => {
    const p = l.split(',');
    return [Number(p[1]), Number(p[2]), Number(p[3])];
});

const jsColors = [];
for (let i = 0; i + 2 < act.length; i += 3) jsColors.push([act[i], act[i + 1], act[i + 2]]);

let ok = jsColors.length === csvColors.length;
for (let i = 0; i < Math.min(jsColors.length, csvColors.length); i++) {
    if (jsColors[i].join() !== csvColors[i].join()) { ok = false; console.log('  色板不一致 @', i); break; }
}
console.log('色板与 CSV 一致:', ok ? 'YES' : 'NO', `(${jsColors.length} 项)`);

// ---------------------------------------------------------------- BMP 对比
const pyBmp = new Uint8Array(readFileSync('C:/FUCKEDA/e6-48color/cmp/收藏到 Wallpape 2025_qual_atkinson_lab.bmp'));
const pOff = new DataView(pyBmp.buffer).getUint32(10, true);
const w = new DataView(pyBmp.buffer).getInt32(18, true);
const h = new DataView(pyBmp.buffer).getInt32(22, true);
const n = new DataView(pyBmp.buffer).getUint32(46, true);
console.log(`\nPython BMP: ${w}x${h}, 颜色表 ${n} 项, 像素偏移 ${pOff}, 共 ${pyBmp.length} 字节`);

// 从 Python BMP 里取出索引（自下而上还原成行优先）
const idx = new Uint8Array(w * h);
const rowSize = ((w * 8 + 31) >> 5) << 2;
for (let y = 0; y < h; y++) {
    const fileRow = h - 1 - y;
    const off = pOff + fileRow * rowSize;
    idx.set(pyBmp.subarray(off, off + w), y * w);
}

const jsBmp = buildBMP(idx, w, h, jsColors);
console.log('JS BMP 大小:', jsBmp.length, jsBmp.length === pyBmp.length ? '(一致)' : '(不一致!)');

let diff = -1;
for (let i = 0; i < Math.min(jsBmp.length, pyBmp.length); i++) {
    if (jsBmp[i] !== pyBmp[i]) { diff = i; break; }
}
if (diff < 0 && jsBmp.length === pyBmp.length) {
    console.log('** BMP 逐字节完全一致 **');
} else {
    console.log('差异 @ 偏移', diff, ' JS=', jsBmp[diff], ' PY=', pyBmp[diff]);
    // 分类看是头部还是像素
    console.log(diff < pOff ? '  -> 头部/颜色表差异' : '  -> 像素差异');
}

// ---------------------------------------------------------------- CRC 抽查
console.log('\nCRC 抽查：', crc32(new TextEncoder().encode('123456789')).toString(16), '(应为 cbf43926)');
