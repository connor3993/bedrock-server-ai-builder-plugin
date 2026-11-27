// ai_build: selection + prompt -> backend -> place blocks
const CFG_PATH = "plugins/ai_build/config.json";
let CFG = { backend: "http://127.0.0.1:8000", pollMs: 1000, batchSize: 800, maxSize: 32, palette: "默认" };
try {
    if (File.exists(CFG_PATH)) {
        const raw = File.readFrom(CFG_PATH);
        if (raw) CFG = Object.assign(CFG, JSON.parse(raw));
    }
} catch (e) { log("[ai_build] config error: " + e); }

const BACKEND = String(CFG.backend).replace(/\/+$/, "");
const POLL_MS = Math.max(300, Number(CFG.pollMs) || 1000);
const BATCH = Math.max(50, Number(CFG.batchSize) || 800);
const MAX_SIZE = Math.max(4, Number(CFG.maxSize) || 32);

const selections = new Map();
const jobs = new Map();
const selecting = new Set();

// ---------- base64 ----------
const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

function b64encode(bytes) {
    let out = "";
    for (let i = 0; i < bytes.length; i += 3) {
        const b0 = bytes[i];
        const b1 = i + 1 < bytes.length ? bytes[i + 1] : 0;
        const b2 = i + 2 < bytes.length ? bytes[i + 2] : 0;
        out += B64[b0 >> 2];
        out += B64[((b0 & 3) << 4) | (b1 >> 4)];
        out += i + 1 < bytes.length ? B64[((b1 & 15) << 2) | (b2 >> 6)] : "=";
        out += i + 2 < bytes.length ? B64[b2 & 63] : "=";
    }
    return out;
}

function b64decode(str) {
    const clean = String(str || "").replace(/[^A-Za-z0-9+/]/g, "");
    const out = new Uint8Array(Math.floor(clean.length * 3 / 4));
    let o = 0;
    for (let i = 0; i < clean.length; i += 4) {
        const c0 = B64.indexOf(clean[i]);
        const c1 = B64.indexOf(clean[i + 1]);
        const c2 = i + 2 < clean.length ? B64.indexOf(clean[i + 2]) : -1;
        const c3 = i + 3 < clean.length ? B64.indexOf(clean[i + 3]) : -1;
        out[o++] = (c0 << 2) | (c1 >> 4);
        if (c2 >= 0) out[o++] = ((c1 & 15) << 4) | (c2 >> 2);
        if (c3 >= 0) out[o++] = ((c2 & 3) << 6) | c3;
    }
    return out.subarray(0, o);
}

function int16ToBytes(arr) {
    const bytes = new Uint8Array(arr.length * 2);
    for (let i = 0; i < arr.length; i++) {
        const v = arr[i] < 0 ? arr[i] + 65536 : arr[i];
        bytes[i * 2] = v & 255;
        bytes[i * 2 + 1] = (v >> 8) & 255;
    }
    return bytes;
}

function bytesToInt16(bytes, n) {
    const arr = new Int16Array(n);
    for (let i = 0; i < n; i++) {
        let v = bytes[i * 2] | (bytes[i * 2 + 1] << 8);
        if (v > 32767) v -= 65536;
        arr[i] = v;
    }
    return arr;
}

// ---------- selection ----------
function keyOf(pl) { return pl.xuid || pl.realName; }

function boxOf(p1, p2) {
    return {
        min: { x: Math.min(p1.x, p2.x), y: Math.min(p1.y, p2.y), z: Math.min(p1.z, p2.z) },
        max: { x: Math.max(p1.x, p2.x), y: Math.max(p1.y, p2.y), z: Math.max(p1.z, p2.z) }
    };
}

function dimOf(pl) { return pl.dimid === undefined ? 0 : pl.dimid; }

const MASK_MAX = 32;

function maskTagOf(pl) {
    const raw = pl ? keyOf(pl) : "__console__";
    return "aibuildmask_" + String(raw).replace(/[^A-Za-z0-9_-]/g, "_");
}

function maskDimPrefix(dim) {
    if (dim === 1) return "execute in nether run ";
    if (dim === 2) return "execute in the_end run ";
    return "";
}

function killMask(pl) {
    if (!pl) return false;
    const tag = maskTagOf(pl);
    try {
        const r = mc.runcmdEx("event entity @e[tag=" + tag + "] csc:despawn");
        if (r && r.success) {
            mc.runcmdEx("tag @e[tag=" + tag + "] add csc_despawning");
            mc.runcmdEx("tag @e[tag=" + tag + "] remove " + tag);
            return true;
        }
        return false;
    } catch (e) {
        log("[ai_build] killMask error: " + e);
        return false;
    }
}

function showMask(pl, sel, mode) {
    if (!pl || !sel || !sel.p1 || !sel.p2) return false;
    const is2d = String(mode || sel.mode || "3d").toLowerCase() === "2d";
    const { min, max } = boxOf(sel.p1, sel.p2);
    const sx = max.x - min.x + 1;
    const sy = max.y - min.y + 1;
    const sz = max.z - min.z + 1;
    const limit = Math.min(MAX_SIZE, MASK_MAX);
    if (sx < 1 || sz < 1 || sx > limit || sz > limit) return false;
    if (!is2d && (sy < 1 || sy > limit)) return false;

    const tag = maskTagOf(pl);
    killMask(pl);

    const cx = (min.x + max.x + 1) / 2;
    const cy = (min.y + max.y + 1) / 2;
    const cz = (min.z + max.z + 1) / 2;
    const dimPre = maskDimPrefix(sel.dim);
    const entityType = is2d ? "csc:mask_2d" : "csc:mask";

    let r = mc.runcmdEx(dimPre + "summon " + entityType + " " + cx + " " + cy + " " + cz);
    if (!r || !r.success) {
        log("[ai_build] mask summon failed (" + entityType + "): " + ((r && r.output) || "unknown"));
        return false;
    }
    r = mc.runcmdEx(dimPre + "tag @e[type=" + entityType + ",x=" + cx + ",y=" + cy + ",z=" + cz + ",r=1.5,tag=!csc_despawning,c=1] add " + tag);
    if (!r || !r.success) {
        log("[ai_build] mask tag failed (" + entityType + "): " + ((r && r.output) || "unknown"));
        return false;
    }
    mc.runcmdEx("event entity @e[tag=" + tag + ",c=1] csc:sx_" + sx);
    if (!is2d) {
        mc.runcmdEx("event entity @e[tag=" + tag + ",c=1] csc:sy_" + sy);
    }
    mc.runcmdEx("event entity @e[tag=" + tag + ",c=1] csc:sz_" + sz);
    return true;
}

function particleLine(dim, a, b) {
    const dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
    const steps = Math.max(Math.abs(dx), Math.abs(dy), Math.abs(dz));
    if (steps === 0) return;
    const step = Math.max(1, Math.ceil(steps / 24));
    for (let i = 0; i <= steps; i += step) {
        const t = i / steps;
        mc.spawnParticle(a.x + dx * t + 0.5, a.y + dy * t + 0.5, a.z + dz * t + 0.5, dim, "minecraft:endrod");
    }
}

function drawSelection(pl, sel) {
    if (!sel || !sel.p1) return;
    const p2 = sel.p2 || sel.p1;
    const { min, max } = boxOf(sel.p1, p2);
    const dim = sel.dim;
    const lo = { x: min.x, y: min.y, z: min.z };
    const hi = { x: max.x + 1, y: max.y + 1, z: max.z + 1 };
    const c = [
        { x: lo.x, y: lo.y, z: lo.z }, { x: hi.x, y: lo.y, z: lo.z },
        { x: hi.x, y: lo.y, z: hi.z }, { x: lo.x, y: lo.y, z: hi.z },
        { x: lo.x, y: hi.y, z: lo.z }, { x: hi.x, y: hi.y, z: lo.z },
        { x: hi.x, y: hi.y, z: hi.z }, { x: lo.x, y: hi.y, z: hi.z }
    ];
    const edges = [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
    for (let i = 0; i < edges.length; i++) {
        const a = c[edges[i][0]], b = c[edges[i][1]];
        particleLine(dim, a, b);
    }
}
// ---------- block states & snapshot ----------
const DEFAULT_BLOCK_VERSION = 17959425;
const SCAN_2D_DOWN = 12;
const SCAN_2D_UP = 20;

function isAirBlock(b) {
    if (!b || b.isAir) return true;
    const bid = String(b.type || b.name || "");
    return !bid || bid === "minecraft:air" || bid === "minecraft:cave_air" || bid === "minecraft:void_air";
}

function isWaterBlock(b) {
    if (!b) return false;
    const bid = String(b.type || b.name || "");
    return bid === "minecraft:water" || bid === "minecraft:flowing_water";
}

function normalizeBlockEntry(raw) {
    if (!raw) return null;
    if (typeof raw === "string") {
        const id = raw.split("[")[0];
        if (!id || id.indexOf("minecraft:") !== 0 || id === "minecraft:air") return null;
        return { id: id, states: {}, version: 0 };
    }
    if (typeof raw === "object") {
        const id = String(raw.id || "").split("[")[0];
        if (!id || id.indexOf("minecraft:") !== 0 || id === "minecraft:air") return null;
        return {
            id: id,
            states: (raw.states && typeof raw.states === "object") ? raw.states : {},
            version: Number(raw.version) || 0
        };
    }
    return null;
}

function canonicalBlockKey(raw) {
    const entry = normalizeBlockEntry(raw);
    if (!entry) return "";
    const id = entry.id === "minecraft:grass" ? "minecraft:grass_block" : entry.id;
    const st = entry.states || {};
    const keys = Object.keys(st).sort();
    if (!keys.length) return id;
    const parts = [];
    for (let i = 0; i < keys.length; i++) {
        const k = keys[i];
        let v = st[k];
        if (Array.isArray(v) && v.length === 2) v = v[1];
        if (typeof v === "boolean") v = v ? 1 : 0;
        parts.push(k + "=" + String(v));
    }
    return id + "|" + parts.join(",");
}

function escapeSnbtStr(s) {
    return '"' + String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
}

function buildBlockNbt(entry) {
    const states = entry.states || {};
    const parts = [];
    for (const k in states) {
        if (!Object.prototype.hasOwnProperty.call(states, k)) continue;
        const v = states[k];
        let valSnbt = null;
        if (Array.isArray(v) && v.length === 2) {
            if (v[0] === "s") valSnbt = escapeSnbtStr(v[1]);
            else if (v[0] === "i") valSnbt = String(Number(v[1]) | 0);
            else if (v[0] === "b") valSnbt = v[1] ? "1b" : "0b";
        } else if (typeof v === "string") {
            valSnbt = escapeSnbtStr(v);
        } else if (typeof v === "boolean") {
            valSnbt = v ? "1b" : "0b";
        } else if (typeof v === "number") {
            valSnbt = String(v | 0);
        }
        if (valSnbt !== null) {
            parts.push(escapeSnbtStr(k) + ":" + valSnbt);
        }
    }
    const ver = Number(entry.version) || DEFAULT_BLOCK_VERSION;
    const snbt = '{"name":' + escapeSnbtStr(entry.id) + ',"states":{' + parts.join(",") + '},"version":' + ver + "}";
    return NBT.parseSNBT(snbt);
}

function setBlockEntry(x, y, z, dim, rawEntry) {
    const entry = normalizeBlockEntry(rawEntry);
    if (!entry) {
        return mc.setBlock(x, y, z, dim, "minecraft:air", 0);
    }
    const hasStates = Object.keys(entry.states || {}).length > 0;
    if (hasStates || entry.version) {
        try {
            const nbt = buildBlockNbt(entry);
            if (nbt && mc.setBlock(new IntPos(x, y, z, dim), nbt)) {
                return true;
            }
        } catch (e) {
            log("[ai_build] setBlock NBT error (" + entry.id + "): " + e);
        }
    }
    return mc.setBlock(x, y, z, dim, entry.id, 0);
}

function resolveScanBox(sel, mode) {
    const { min, max } = boxOf(sel.p1, sel.p2);
    const is2d = String(mode || sel.mode || "3d").toLowerCase() === "2d";
    if (!is2d) {
        return { min: min, max: max };
    }
    const sx = max.x - min.x + 1;
    const sz = max.z - min.z + 1;
    const maxSy = Math.min(MAX_SIZE, Math.max(1, Math.floor((MAX_SIZE * MAX_SIZE * MAX_SIZE) / (sx * sz))));
    const worldMinY = sel.dim === 0 ? -64 : 0;
    const worldMaxY = sel.dim === 0 ? 319 : (sel.dim === 1 ? 127 : 255);
    const probeLo = Math.max(worldMinY, min.y - SCAN_2D_DOWN);
    const probeHi = Math.min(worldMaxY, max.y + SCAN_2D_UP);

    let foundMinY = min.y;
    let foundMaxY = max.y;

    for (let z = min.z; z <= max.z; z++) {
        for (let x = min.x; x <= max.x; x++) {
            for (let y = probeHi; y > foundMaxY; y--) {
                let b;
                try { b = mc.getBlock(x, y, z, sel.dim); } catch (e) { b = null; }
                if (!isAirBlock(b)) {
                    foundMaxY = y;
                    break;
                }
            }
            let atBase;
            try { atBase = mc.getBlock(x, min.y, z, sel.dim); } catch (e) { atBase = null; }
            if (isAirBlock(atBase) || isWaterBlock(atBase)) {
                for (let y = min.y - 1; y >= probeLo; y--) {
                    let b;
                    try { b = mc.getBlock(x, y, z, sel.dim); } catch (e) { b = null; }
                    if (!isAirBlock(b) && !isWaterBlock(b)) {
                        if (y < foundMinY) foundMinY = y;
                        break;
                    }
                    if (y === probeLo && y < foundMinY) {
                        foundMinY = y;
                    }
                }
            }
        }
    }

    let scanMinY = foundMinY;
    let scanMaxY = foundMaxY;
    if (scanMaxY - scanMinY + 1 > maxSy) {
        const keepDown = Math.min(min.y - scanMinY, Math.floor(maxSy / 3));
        scanMinY = min.y - keepDown;
        scanMaxY = scanMinY + maxSy - 1;
    }
    return {
        min: { x: min.x, y: scanMinY, z: min.z },
        max: { x: max.x, y: scanMaxY, z: max.z }
    };
}

function snapshotBox(box, dim) {
    const min = box.min, max = box.max;
    const sx = max.x - min.x + 1;
    const sy = max.y - min.y + 1;
    const sz = max.z - min.z + 1;
    const total = sx * sy * sz;
    const palette = [];
    const index = new Map();
    const arr = new Int16Array(total);
    let filled = 0;
    for (let y = 0; y < sy; y++) {
        for (let z = 0; z < sz; z++) {
            for (let x = 0; x < sx; x++) {
                const i = y * sx * sz + z * sx + x;
                let b;
                try { b = mc.getBlock(min.x + x, min.y + y, min.z + z, dim); } catch (e) { b = null; }
                if (isAirBlock(b)) { arr[i] = -1; continue; }
                const bid = String(b.type || b.name || "");
                let states = {};
                try { states = b.getBlockState() || {}; } catch (e) { states = {}; }
                const k = bid + "|" + JSON.stringify(states);
                let idx = index.get(k);
                if (idx === undefined) {
                    idx = palette.length;
                    palette.push({ id: bid, states: states });
                    index.set(k, idx);
                }
                arr[i] = idx;
                filled++;
            }
        }
    }
    return { size: [sx, sy, sz], palette: palette, blocks: b64encode(int16ToBytes(arr)), filled: filled };
}

// ---------- http ----------
function postJSON(url, obj, cb) {
    network.httpPost(url, {}, JSON.stringify(obj), "application/json", function (status, body) {
        cb(status, body);
    });
}

function getJSON(url, cb) {
    network.httpGet(url, {}, function (status, body) {
        cb(status, body);
    });
}

function tell(pl, msg) {
    if (pl) { try { pl.tell(msg); } catch (e) { log("[ai_build] " + msg); } }
    else log("[ai_build] " + msg);
}

function bar(pl, msg) {
    if (!pl) return;
    const text = String(msg).replace(/"/g, "'");
    try { mc.runcmdEx('title "' + pl.realName + '" actionbar "' + text + '"'); } catch (e) { }
}

function clearBar(pl) {
    bar(pl, "");
}

// ---------- jobs ----------
function stopJob(pl, quiet) {
    const k = pl ? keyOf(pl) : "__console__";
    const job = jobs.get(k);
    if (!job) return false;
    if (job.timer) clearInterval(job.timer);
    if (job.placeTimer) clearInterval(job.placeTimer);
    jobs.delete(k);
    if (!quiet) {
        tell(pl, "§e已取消当前任务");
        if (job.snapshot) restoreSnapshot(job);
    }
    clearBar(pl);
    return true;
}

function submit(pl, prompt, size, palette, mode) {
    const sel = selections.get(keyOf(pl));
    if (!sel || !sel.p1 || !sel.p2) { tell(pl, "§c先用木棍右键两个方块选点"); return; }
    const normMode = String(mode || sel.mode || "3d").toLowerCase();
    const is2d = normMode === "2d";
    const { min, max } = boxOf(sel.p1, sel.p2);
    const sx = max.x - min.x + 1, sy = max.y - min.y + 1, sz = max.z - min.z + 1;
    if (sx > MAX_SIZE || sz > MAX_SIZE || (!is2d && sy > MAX_SIZE)) {
        killMask(pl);
        tell(pl, is2d
            ? ("§c2D 平面 " + sx + "x" + sz + " 超过上限 " + MAX_SIZE)
            : ("§c选区 " + sx + "x" + sy + "x" + sz + " 超过上限 " + MAX_SIZE));
        return;
    }

    const scanBox = resolveScanBox(sel, normMode);
    const scanSx = scanBox.max.x - scanBox.min.x + 1;
    const scanSy = scanBox.max.y - scanBox.min.y + 1;
    const scanSz = scanBox.max.z - scanBox.min.z + 1;
    tell(pl, is2d
        ? ("§7正在扫描 2D 地形（" + scanSx + "x" + scanSy + "x" + scanSz + "，y=" + scanBox.min.y + ".." + scanBox.max.y + "）...")
        : "§7正在扫描选区...");

    const ctx = snapshotBox(scanBox, sel.dim);
    const body = {
        player_id: pl ? String(pl.realName || "") : "",
        prompt: String(prompt || "").trim() || "改造这个区域",
        palette: palette || CFG.palette,
        mode: normMode,
        context: ctx
    };
    postJSON(BACKEND + "/v1/build", body, function (status, resp) {
        if (status !== 200 && status !== 202) {
            killMask(pl);
            tell(pl, "§c提交失败 HTTP " + status + ": " + String(resp).slice(0, 160));
            return;
        }
        let r;
        try { r = JSON.parse(resp); } catch (e) { killMask(pl); tell(pl, "§c返回格式错误"); return; }
        startPolling(pl, r.task_id, scanBox.min, sel.dim, [scanSx, scanSy, scanSz], normMode, ctx);
    });
}


function pullChunks(pl, job) {
    const k = pl ? keyOf(pl) : "__console__";
    if (jobs.get(k) !== job) return;
    getJSON(BACKEND + "/v1/build/" + encodeURIComponent(job.taskId) + "/chunks?after=" + job.chunkSeq, function (status, body) {
        if (status !== 200) return;
        if (jobs.get(k) !== job) return;
        let r;
        try { r = JSON.parse(body); } catch (e) { return; }
        const items = r.items || [];
        const o = job.origin, dim = job.dim;
        let placedNow = 0;
        for (let n = 0; n < items.length; n++) {
            const item = items[n] || {};
            const sets = item.set || {};
            for (const pos in sets) {
                const parts = pos.split(",");
                const entry = normalizeBlockEntry(sets[pos]);
                if (!entry) continue;
                try {
                    setBlockEntry(o.x + Number(parts[0]), o.y + Number(parts[1]), o.z + Number(parts[2]), dim, entry);
                    job.placed[pos] = canonicalBlockKey(entry);
                    placedNow++;
                } catch (e) { }
            }
            const removes = item.remove || [];
            for (let m = 0; m < removes.length; m++) {
                const key = String(removes[m]);
                const parts = key.split(",");
                try {
                    mc.setBlock(o.x + Number(parts[0]), o.y + Number(parts[1]), o.z + Number(parts[2]), dim, "minecraft:air", 0);
                    job.placed[key] = "";
                } catch (e) { }
            }
        }
        job.placedCount = (job.placedCount || 0) + placedNow;
        job.chunkSeq = r.seq || job.chunkSeq;
        if (placedNow > 0) {
            const now = Date.now();
            if (!job.lastTell || now - job.lastTell >= 2500) {
                job.lastTell = now;
                tell(pl, "§a已放置 " + job.placedCount + " 块（第 " + job.chunkSeq + " 批）");
            }
        }
    });
}

function restoreSnapshot(job) {
    const ctx = job.snapshot;
    if (!ctx || !ctx.size || !ctx.blocks) return;
    const sx = ctx.size[0], sy = ctx.size[1], sz = ctx.size[2];
    const total = sx * sy * sz;
    let arr;
    try { arr = bytesToInt16(b64decode(ctx.blocks), total); } catch (e) { return; }
    const palette = ctx.palette || [];
    const o = job.origin, dim = job.dim;
    let i = 0;
    const timer = setInterval(function () {
        for (let n = 0; n < 2000 && i < total; n++, i++) {
            const v = arr[i];
            const p = v >= 0 ? palette[v] : null;
            const x = i % sx;
            const y = Math.floor(i / (sx * sz));
            const z = Math.floor(i / sx) % sz;
            try {
                if (!p) mc.setBlock(o.x + x, o.y + y, o.z + z, dim, "minecraft:air", 0);
                else setBlockEntry(o.x + x, o.y + y, o.z + z, dim, p);
            } catch (e) { }
        }
        if (i >= total) clearInterval(timer);
    }, 30);
}

function startPolling(pl, taskId, origin, dim, size, mode, snapshotCtx) {
    stopJob(pl, true);
    const k = pl ? keyOf(pl) : "__console__";
    const job = { taskId: taskId, origin: origin, dim: dim, size: size, mode: mode || "3d", startedAt: Date.now(), timer: undefined, placeTimer: undefined, chunkSeq: 0, placed: {}, snapshot: snapshotCtx || null };
    jobs.set(k, job);
    buildModes.delete(k);
    tell(pl, "§a任务已提交: " + taskId);
    job.timer = setInterval(function () {
        if (jobs.get(k) !== job) return;
        pullChunks(pl, job);
        getJSON(BACKEND + "/v1/build/" + encodeURIComponent(taskId), function (status, body) {
            if (status !== 200) return;
            let r;
            try { r = JSON.parse(body); } catch (e) { return; }
            const pr = r.progress || {};
            const stageName = pr.stage === "queued" ? "排队中" : (pr.stage === "converting" ? "转换中" : "生成中");
            const secs = Math.floor((Date.now() - job.startedAt) / 1000);
            let toolName = String(pr.tool || "…").replace(/^mcp__schematic_architect__/, "");
            bar(pl, "§b" + stageName + " §f" + (pr.steps || 0) + " 步 §7" + toolName + " §8· §f" + secs + "s");
            if (r.status === "done") {
                clearInterval(job.timer); job.timer = undefined;
                tell(pl, "§a生成完成，开始放置");
                placeResult(pl, job, r.result);
            } else if (r.status === "failed") {
                clearInterval(job.timer); job.timer = undefined;
                jobs.delete(k);
                killMask(pl);
                clearBar(pl);
                tell(pl, "§c生成失败: " + (r.error || "unknown"));
            }
        });
    }, POLL_MS);
}

function placeResult(pl, job, result) {
    const k = pl ? keyOf(pl) : "__console__";
    if (!result || !result.size || !result.blocks) { tell(pl, "§c产物为空"); jobs.delete(k); killMask(pl); clearBar(pl); return; }
    if (job.size) {
        const okSize = job.mode === "2d"
            ? (result.size[0] === job.size[0] && result.size[2] === job.size[2])
            : result.size.join("x") === job.size.join("x");
        if (!okSize) {
            tell(pl, "§c尺寸不一致：选区 " + job.size.join("x") + " vs 产物 " + result.size.join("x") + "，已中止");
            jobs.delete(k);
            killMask(pl);
            clearBar(pl);
            return;
        }
    }

    const sx = result.size[0], sy = result.size[1], sz = result.size[2];
    const total = sx * sy * sz;
    let arr;
    try { arr = bytesToInt16(b64decode(result.blocks), total); } catch (e) {
        tell(pl, "§c产物解析失败"); jobs.delete(k); killMask(pl); clearBar(pl); return;
    }
    const palette = result.palette || [];

    // 以任务开始时的选区为基线，只补最终结果与当前已增量放置结果之间的差异。
    let originalArr = null;
    let originalPalette = null;
    if (job.snapshot && job.snapshot.size && job.snapshot.blocks) {
        try {
            const osx = job.snapshot.size[0];
            const osy = job.snapshot.size[1];
            const osz = job.snapshot.size[2];
            originalArr = bytesToInt16(b64decode(job.snapshot.blocks), osx * osy * osz);
            originalPalette = job.snapshot.palette || [];
        } catch (e) {
            originalArr = null;
            originalPalette = null;
        }
    }
    if (!job.placed) job.placed = {};

    const patches = [];
    let unknownFinal = 0;
    for (let i = 0; i < total; i++) {
        const x = i % sx;
        const y = Math.floor(i / (sx * sz));
        const z = Math.floor(i / sx) % sz;
        const pkey = x + "," + y + "," + z;

        const rawFinal = arr[i] >= 0 ? palette[arr[i]] : null;
        const finalEntry = normalizeBlockEntry(rawFinal);
        if (arr[i] >= 0 && !finalEntry) {
            unknownFinal++;
            continue;
        }
        const finalKey = canonicalBlockKey(finalEntry);
        const originalEntry = originalArr && i < originalArr.length && originalArr[i] >= 0
            ? originalPalette[originalArr[i]]
            : null;
        const originalKey = canonicalBlockKey(originalEntry);
        const currentKey = Object.prototype.hasOwnProperty.call(job.placed, pkey)
            ? String(job.placed[pkey] || "")
            : originalKey;

        if (currentKey === finalKey) continue;
        patches.push({ x: x, y: y, z: z, entry: finalEntry, key: finalKey });
    }

    const finish = function (placed, removed, skipped) {
        const staged = Number(job.placedCount) || 0;
        jobs.delete(k);
        killMask(pl);
        tell(pl, "§a建造完成，增量 " + staged + " 块" +
            (placed ? "，补齐 " + placed + " 块" : "") +
            (removed ? "，清理 " + removed + " 块" : "") +
            (skipped ? "，跳过 " + skipped + " 个未知方块" : ""));
        clearBar(pl);
        log("[ai_build] placed " + (staged + placed) + " blocks for " + (pl ? pl.realName : "console"));
    };

    if (!patches.length) {
        finish(0, 0, unknownFinal);
        return;
    }

    let index = 0;
    let placed = 0;
    let removed = 0;
    let skipped = unknownFinal;
    job.placeTimer = setInterval(function () {
        if (jobs.get(k) !== job) { clearInterval(job.placeTimer); return; }
        const end = Math.min(patches.length, index + BATCH);
        for (; index < end; index++) {
            const p = patches[index];
            try {
                const pkey = p.x + "," + p.y + "," + p.z;
                if (p.entry) {
                    setBlockEntry(job.origin.x + p.x, job.origin.y + p.y, job.origin.z + p.z, job.dim, p.entry);
                    job.placed[pkey] = p.key;
                    placed++;
                } else {
                    mc.setBlock(job.origin.x + p.x, job.origin.y + p.y, job.origin.z + p.z, job.dim, "minecraft:air", 0);
                    job.placed[pkey] = "";
                    removed++;
                }
            } catch (e) { }
        }
        if (index < patches.length) {
            bar(pl, "§a增量补齐 " + index + "/" + patches.length + " §7(" + Math.floor(index * 100 / patches.length) + "%)");
            return;
        }
        clearInterval(job.placeTimer);
        job.placeTimer = undefined;
        finish(placed, removed, skipped);
    }, 30);
}

// ---------- form ----------
function openForm(pl, sel) {
    const b = boxOf(sel.p1, sel.p2);
    const sx = b.max.x - b.min.x + 1, sy = b.max.y - b.min.y + 1, sz = b.max.z - b.min.z + 1;
    const mode = String(buildModes.get(keyOf(pl)) || sel.mode || "3d").toLowerCase();
    const sizeText = mode === "2d" ? (sx + "x" + sz) : (sx + "x" + sy + "x" + sz);
    setTimeout(function () {
        const form = mc.newCustomForm();
        form.setTitle("AI 建造 " + sizeText + " · " + mode.toUpperCase());
        form.addInput("提示词", "例如：一座中世纪小酒馆", "");
        form.addDropdown("风格", ["默认"], 0);
        pl.sendForm(form, function (player, data) {
            if (!data) {
                selections.delete(keyOf(player));
                killMask(player);
                return;
            }
            const prompt = data[0];
            const styles = ["默认"];
            const palette = styles[data[1]] || CFG.palette;
            const mode = buildModes.get(keyOf(player)) || "3d";
            submit(player, prompt, Math.max(sx, sy, sz), palette, mode);
        });
    }, 300);
}

// ---------- stick wand ----------
let stickBusy = false;

function stickPoint(pl, pos) {
    const k = keyOf(pl);
    const p = { x: Math.floor(pos.x), y: Math.floor(pos.y), z: Math.floor(pos.z) };
    const sel = selections.get(k);
    if (!sel || !sel.p1 || sel.p2) {
        killMask(pl);
        selections.set(k, { p1: p, p2: null, dim: dimOf(pl) });
        tell(pl, "§a点1: " + p.x + ", " + p.y + ", " + p.z + " §7(再右键一个方块选点2)");
        drawSelection(pl, selections.get(k));
        return;
    }
    if (sel.p1.x === p.x && sel.p1.y === p.y && sel.p1.z === p.z) {
        tell(pl, "§e和点1是同一个方块，换个方块再右键");
        return;
    }
    sel.p2 = p;
    sel.dim = dimOf(pl);
    const mode = String(buildModes.get(k) || "3d").toLowerCase();
    sel.mode = mode;
    const b = boxOf(sel.p1, sel.p2);
    const sx = b.max.x - b.min.x + 1, sy = b.max.y - b.min.y + 1, sz = b.max.z - b.min.z + 1;
    if (mode === "2d") {
        tell(pl, "§a点2: " + p.x + ", " + p.y + ", " + p.z + "（2D 平面 " + sx + "x" + sz + "，高度不限）");
    } else {
        tell(pl, "§a点2: " + p.x + ", " + p.y + ", " + p.z + "（选区 " + sx + "x" + sy + "x" + sz + "）");
    }
    if (!showMask(pl, sel, mode)) {
        drawSelection(pl, sel);
    }
    openForm(pl, sel);
}

const stickLast = new Map();
const buildModes = new Map();

mc.listen("onUseItemOn", function (pl, item, block, side, pos) {
    if (!pl || !item || item.type !== "minecraft:stick") return true;
    const k = keyOf(pl);
    if (!buildModes.has(k)) return true;
    const now = Date.now();
    if (now - (stickLast.get(k) || 0) < 400) return true;
    stickLast.set(k, now);
    if (!block || !block.pos) return true;
    try {
        stickPoint(pl, block.pos);
    } catch (e) {
        log("[ai_build] stick error: " + e);
    }
    return true;
});
// ---------- events ----------
function registerCmd(name, desc, cb) {
    const cmd = mc.newCommand(name, desc, PermType.Any);
    cmd.overload([]);
    cmd.setCallback(function (_cmd, origin, output) {
        try { cb(origin.player, output); } catch (e) {
            log("[ai_build] command " + name + " error: " + e);
            output.error("执行失败: " + e);
        }
    });
    const ok = cmd.setup();
    log("[ai_build] /" + name + " registered: " + ok);
}

function enterSelect(pl, mode, output) {
    buildModes.set(keyOf(pl), mode);
    stopJob(pl, true);
    killMask(pl);
    selections.delete(keyOf(pl));
    const msg = "§a" + (mode === "2d" ? "2D 平面" : "3D 立体") + "模式：手持木棍右键两个方块选点";
    if (output) output.success(msg); else tell(pl, msg);
}

function openModeForm(pl) {
    const form = mc.newSimpleForm();
    form.setTitle("AI 建造 · 选择模式");
    form.setContent("3D：立体改造，依照地形、保留地面\n2D：薄片当画布，自由发挥、填满平面");
    form.addButton("3D 立体（依照地形）");
    form.addButton("2D 平面（自由发挥）");
    pl.sendForm(form, function (player, id) {
        if (id === null || id === undefined) return;
        enterSelect(player, id === 1 ? "2d" : "3d", null);
    });
}
mc.listen("onServerStarted", function () {

    const buildCmd = mc.newCommand("build", "AI build: /build 2d | /build 3d, then stick", PermType.Any);
    buildCmd.optional("mode", ParamType.String);
    buildCmd.overload([]);
    buildCmd.overload(["mode"]);
    buildCmd.setCallback(function (_cmd, origin, output, result) {
        const pl = origin.player;
        if (!pl) { output.error("需要玩家"); return; }
        const m = String((result && result.mode) || "").toLowerCase();
        if (m && m !== "2d" && m !== "3d") { output.error("用法：/build 或 /build 2d 或 /build 3d"); return; }
        if (!m) { openModeForm(pl); return; }
        enterSelect(pl, m, output);
    });
    log("[ai_build] /build registered: " + buildCmd.setup());
    registerCmd("buildcancel", "AI build: cancel", function (pl, output) {
        if (!pl) { output.error("控制台没有任务"); return; }
        buildModes.delete(keyOf(pl));
        selections.delete(keyOf(pl));
        const stopped = stopJob(pl, true);
        const clearedMask = killMask(pl);
        if (stopped) output.success(clearedMask ? "已取消任务并清除遮罩" : "已取消");
        else output.success(clearedMask ? "已清除遮罩与选点" : "已清空选点");
    });

    registerCmd("buildstatus", "AI build: status", function (pl, output) {
        if (!pl) { output.error("控制台没有任务"); return; }
        const job = jobs.get(keyOf(pl));
        if (!job) output.success("当前没有任务");
        else output.success("任务 " + job.taskId + " 进行中");
    });






    log("[ai_build] ready, backend=" + BACKEND);
});