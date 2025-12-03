#!/usr/bin/env node
/*
 * 批量处理 .gtbl 文件脚本
 * 步骤：
 * 1. 以二进制读取某个文件夹下的所有 .gtbl 文件
 * 2. 使用 gzip 解压得到 JSON 文本
 * 3. 假定 JSON 的 value 为 PNG 图片的二进制（或 base64 字符串），给每个 value 加上 data: 前缀
 * 4. 将 PNG 转为 WEBP
 * 5. 将处理后的结构重新 gzip，写回 .gtbl
 *
 * 注意：
 * - 该脚本对 JSON 结构有一定假设：顶层是对象，value 为字符串或 Buffer，可作为 PNG 数据。
 * - 如果你实际的 JSON 结构不同，可能需要在 transformJson 函数中定制处理逻辑。
 */

const fs = require("fs");
const path = require("path");
const zlib = require("zlib");
const sharp = require("sharp");

/**
 * 递归遍历目录，收集所有 .gtbl 文件
 * @param {string} dir
 * @returns {string[]}
 */
function collectGtblFiles(dir) {
    /** @type {string[]} */
    const result = [];

    function walk(current) {
        const entries = fs.readdirSync(current, { withFileTypes: true });
        for (const entry of entries) {
            const fullPath = path.join(current, entry.name);
            if (entry.isDirectory()) {
                walk(fullPath);
            } else if (entry.isFile() && fullPath.toLowerCase().endsWith(".gtbl")) {
                result.push(fullPath);
            }
        }
    }

    walk(dir);
    return result;
}

/**
 * 将 JSON 对象中的所有字符串 value 当作 PNG 数据处理：
 * 1. 如果看起来像 base64，则解码，否则按 utf8 文本处理（很可能不符合预期，需要按你的实际格式调整）
 * 2. 使用 sharp 将 PNG Buffer 转为 WEBP Buffer
 * 3. 将 WEBP Buffer 转为 base64，并加上 `data:image/webp;base64,` 前缀
 *
 * 这里的逻辑是一个“模板”，你可以根据实际 JSON 结构修改。
 */
async function transformJson(obj) {
    async function transformValue(value) {
        if (typeof value !== "string") return value;

        // 尝试把字符串当作 base64 解码，如果失败就当作普通文本 Buffer
        let pngBuffer;
        const base64Match = value.match(/^data:.*;base64,(.+)$/);
        try {
            if (base64Match) {
                pngBuffer = Buffer.from(base64Match[1], "base64");
            } else if (/^[0-9A-Za-z+/=]+$/.test(value)) {
                // 粗略判断为 base64
                pngBuffer = Buffer.from(value, "base64");
            } else {
                pngBuffer = Buffer.from(value, "utf8");
            }
        } catch (e) {
            // 解码失败就保持原值
            return value;
        }

        try {
            const webpBuffer = await sharp(pngBuffer).webp({ lossless: true }).toBuffer();
            const webpBase64 = webpBuffer.toString("base64");
            return webpBase64;
        } catch (e) {
            console.warn("PNG -> WEBP 失败，保留原值:", e.message);
            return value;
        }
    }

    async function walk(node) {
        if (Array.isArray(node)) {
            const out = [];
            for (const item of node) {
                out.push(await walk(item));
            }
            return out;
        }
        if (node && typeof node === "object") {
            const out = {};
            for (const [k, v] of Object.entries(node)) {
                if (typeof v === "string") {
                    out[k] = await transformValue(v);
                } else {
                    out[k] = await walk(v);
                }
            }
            return out;
        }
        return node;
    }

    return await walk(obj);
}

/**
 * 处理单个 .gtbl 文件
 * @param {string} inputPath
 * @param {string} outputPath
 */
async function processGtblFile(inputPath, outputPath) {
    const raw = fs.readFileSync(inputPath); // 二进制读取

    // 解 gzip → JSON
    let jsonStr;
    try {
        const unzipped = zlib.gunzipSync(raw);
        jsonStr = unzipped.toString("utf8");
    } catch (e) {
        console.error(`[失败] 解压 ${inputPath} 时出错:`, e.message);
        return;
    }

    /** @type {any} */
    let json;
    try {
        json = JSON.parse(jsonStr);
    } catch (e) {
        console.error(`[失败] 解析 JSON ${inputPath} 时出错:`, e.message);
        return;
    }

    const transformed = await transformJson(json);
    const newJsonStr = JSON.stringify(transformed);

    // 重新 gzip
    const gzipped = zlib.gzipSync(Buffer.from(newJsonStr, "utf8"));

    const outDir = path.dirname(outputPath);
    if (!fs.existsSync(outDir)) {
        fs.mkdirSync(outDir, { recursive: true });
    }
    fs.writeFileSync(outputPath, gzipped);

    console.log(`[成功] ${inputPath} -> ${outputPath}`);
}

async function main() {
    const args = process.argv.slice(2);
    if (args.length < 1 || args.includes("-h") || args.includes("--help")) {
        console.log("用法: node tools/UnzipGtbl.js <inputDir> [outputDir]");
        console.log("  inputDir : 包含 .gtbl 文件的目录");
        console.log("  outputDir: 输出目录（可选，默认覆盖 inputDir 内的文件名，建议先使用单独目录测试）");
        process.exit(0);
    }

    const inputDir = path.resolve(args[0]);
    const outputDir = args[1] ? path.resolve(args[1]) : inputDir;

    if (!fs.existsSync(inputDir) || !fs.statSync(inputDir).isDirectory()) {
        console.error("输入目录不存在或不是目录:", inputDir);
        process.exit(1);
    }

    const files = collectGtblFiles(inputDir);
    if (files.length === 0) {
        console.log("未在目录中找到任何 .gtbl 文件:", inputDir);
        return;
    }

    console.log(`共找到 ${files.length} 个 .gtbl 文件，将逐个处理...`);

    for (const file of files) {
        const relative = path.relative(inputDir, file);
        const outPath = path.join(outputDir, relative);
        try {
            await processGtblFile(file, outPath);
        } catch (e) {
            console.error(`[失败] 处理 ${file} 时抛出异常:`, e);
        }
    }

    console.log("全部处理完成");
}

if (require.main === module) {
    main().catch((err) => {
        console.error("脚本运行出错:", err);
        process.exit(1);
    });
}
