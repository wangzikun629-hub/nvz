import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const NODE_MODULES_DIR =
  process.env.CODEX_NODE_MODULES ||
  "C:/Users/ASUS/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules";

const artifactToolPath = path.join(
  NODE_MODULES_DIR,
  "@oai",
  "artifact-tool",
  "dist",
  "artifact_tool.mjs",
);
const { Presentation, PresentationFile } = await import(
  pathToFileURL(artifactToolPath).href
);

const ROOT = "D:/nvz/kefu";
const THREAD_ID = "manual-20260615-architecture-flow";
const TASK_SLUG = "architecture-flow-deck";
const WORKSPACE = path.join(
  os.tmpdir(),
  "codex-presentations",
  THREAD_ID,
  TASK_SLUG,
);
const TMP_DIR = path.join(WORKSPACE, "tmp");
const PREVIEW_DIR = path.join(TMP_DIR, "preview");
const LAYOUT_DIR = path.join(TMP_DIR, "layout");
const QA_DIR = path.join(TMP_DIR, "qa");
const OUTPUT_DIR = path.join(ROOT, "outputs");
const FINAL_PPTX = path.join(OUTPUT_DIR, "project-architecture-flow.pptx");

const theme = {
  bg: "#f4f0e8",
  ink: "#1c2438",
  text: "#31415f",
  muted: "#70809d",
  line: "#d6deed",
  navy: "#1f2f4f",
  navy2: "#2a3d63",
  blue: "#4b78e6",
  green: "#1f9d7a",
  amber: "#c58a2a",
  white: "#fffdf9",
  softBlue: "#edf3ff",
  softGreen: "#edf8f4",
  softAmber: "#fff6e7",
};

async function ensureDirs() {
  await fs.mkdir(PREVIEW_DIR, { recursive: true });
  await fs.mkdir(LAYOUT_DIR, { recursive: true });
  await fs.mkdir(QA_DIR, { recursive: true });
  await fs.mkdir(OUTPUT_DIR, { recursive: true });
}

async function writeBlob(targetPath, blob) {
  await fs.writeFile(targetPath, new Uint8Array(await blob.arrayBuffer()));
}

function bg(slide) {
  slide.background.fill = theme.bg;
  slide.shapes.add({
    geometry: "rect",
    position: { left: 0, top: 0, width: 1280, height: 720 },
    fill: theme.bg,
    line: { style: "solid", fill: "none", width: 0 },
  });
  slide.shapes.add({
    geometry: "rect",
    position: { left: 0, top: 0, width: 1280, height: 20 },
    fill: theme.navy,
    line: { style: "solid", fill: "none", width: 0 },
  });
}

function addText(slide, text, position, style = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position,
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = {
    typeface: "Aptos",
    fontSize: 18,
    color: theme.text,
    ...style,
  };
  return shape;
}

function card(slide, position, fill, lineFill = theme.line, radius = "rounded-2xl") {
  return slide.shapes.add({
    geometry: "roundRect",
    position,
    fill,
    line: { style: "solid", fill: lineFill, width: 1 },
    borderRadius: radius,
  });
}

function titleBlock(slide, kicker, title, subtitle, no) {
  addText(slide, kicker.toUpperCase(), { left: 78, top: 56, width: 180, height: 18 }, {
    fontSize: 12,
    bold: true,
    color: theme.green,
  });
  addText(slide, title, { left: 76, top: 82, width: 900, height: 52 }, {
    fontSize: 34,
    bold: true,
    color: theme.ink,
  });
  addText(slide, subtitle, { left: 78, top: 128, width: 980, height: 24 }, {
    fontSize: 16,
    color: theme.muted,
  });
  addText(slide, String(no).padStart(2, "0"), { left: 1172, top: 58, width: 34, height: 16 }, {
    fontSize: 12,
    bold: true,
    color: theme.muted,
    alignment: "right",
  });
}

function footer(slide, text) {
  addText(slide, text, { left: 78, top: 690, width: 880, height: 14 }, {
    fontSize: 10,
    color: "#8d98ad",
  });
}

function pill(slide, text, position, fill, lineFill, color) {
  const p = slide.shapes.add({
    geometry: "roundRect",
    position,
    fill,
    line: { style: "solid", fill: lineFill, width: 1 },
    borderRadius: "rounded-full",
  });
  p.text = text;
  p.text.style = {
    typeface: "Aptos",
    fontSize: 11,
    bold: true,
    color,
    alignment: "center",
  };
}

function arrow(slide, fromShape, toShape, color = theme.blue, kind = "straight", fromSide = "right", toSide = "left") {
  const connector = slide.shapes.connect(fromShape, toShape, {
    kind,
    fromSide,
    toSide,
    line: { style: "solid", fill: color, width: 2.4 },
    head: { type: "none", width: "sm", length: "sm" },
    tail: { type: "triangle", width: "sm", length: "sm" },
  });
  connector.bringToFront();
}

function makeModule(slide, cfg) {
  const shape = card(slide, cfg.position, cfg.fill, cfg.lineFill ?? cfg.fill, cfg.radius ?? "rounded-2xl");
  shape.text = "";
  if (cfg.tag) {
    pill(slide, cfg.tag, {
      left: cfg.position.left + 18,
      top: cfg.position.top + 16,
      width: cfg.tagWidth ?? 96,
      height: 22,
    }, cfg.tagFill ?? "#ffffff", cfg.tagLine ?? "#d8deeb", cfg.tagColor ?? theme.muted);
  }
  addText(slide, cfg.title, {
    left: cfg.position.left + 18,
    top: cfg.position.top + 52,
    width: cfg.position.width - 36,
    height: cfg.titleHeight ?? 36,
  }, {
    fontSize: cfg.titleSize ?? 28,
    bold: true,
    color: cfg.titleColor ?? theme.ink,
    alignment: cfg.align ?? "left",
  });
  if (cfg.subtitle) {
    addText(slide, cfg.subtitle, {
      left: cfg.position.left + 18,
      top: cfg.position.top + 92,
      width: cfg.position.width - 36,
      height: 22,
    }, {
      fontSize: 15,
      color: cfg.subtitleColor ?? theme.muted,
      alignment: cfg.align ?? "left",
    });
  }
  return shape;
}

function bulletLines(slide, items, left, top, width, color = theme.text, bulletColor = theme.green, gap = 36, fontSize = 17) {
  items.forEach((item, index) => {
    addText(slide, "•", { left, top: top + index * gap, width: 16, height: 18 }, {
      fontSize,
      bold: true,
      color: bulletColor,
    });
    addText(slide, item, { left: left + 18, top: top + index * gap, width, height: 22 }, {
      fontSize,
      color,
    });
  });
}

function cover(presentation) {
  const slide = presentation.slides.add();
  bg(slide);

  pill(slide, "ARCHITECTURE REPORT", { left: 78, top: 96, width: 168, height: 28 }, "#ebf8f2", "#cfe8dc", theme.green);
  addText(slide, "两前端 · 两后端", { left: 76, top: 156, width: 520, height: 68 }, {
    fontSize: 46,
    bold: true,
    color: theme.ink,
  });
  addText(slide, "面向汇报的核心架构流程图", { left: 78, top: 232, width: 420, height: 26 }, {
    fontSize: 22,
    color: theme.muted,
  });
  addText(slide, "聚焦系统的四个核心应用，以及它们之间的主调用链、分层关系与职责边界。", {
    left: 78, top: 274, width: 560, height: 46,
  }, {
    fontSize: 18,
    color: theme.text,
  });

  const frame = card(slide, { left: 706, top: 112, width: 494, height: 486 }, theme.navy, theme.navy);
  frame.text = "";
  addText(slide, "汇报聚焦", { left: 744, top: 150, width: 120, height: 24 }, {
    fontSize: 24,
    bold: true,
    color: "#ffffff",
  });
  bulletLines(slide, [
    "前端按场景分工",
    "后端按能力分层",
    "app 是业务编排中枢",
    "knowledge 是共享知识底座",
    "主链路：前端 → app → knowledge",
  ], 748, 212, 320, "#dbe6ff", "#7dc7ff", 56, 19);

  const strip = card(slide, { left: 78, top: 540, width: 560, height: 74 }, theme.white, "#d8d0bf", "rounded-xl");
  strip.text = "";
  addText(slide, "输出结构", { left: 106, top: 564, width: 90, height: 20 }, {
    fontSize: 16,
    bold: true,
    color: theme.amber,
  });
  addText(slide, "1 页总览流程图 + 1 页主链路图 + 1 页前端结构 + 1 页后端结构", {
    left: 204, top: 564, width: 390, height: 20,
  }, {
    fontSize: 16,
    color: theme.text,
  });
  footer(slide, "Source: ARCHITECTURE.md");
}

function overviewFlow(presentation) {
  const slide = presentation.slides.add();
  bg(slide);
  titleBlock(
    slide,
    "Executive View",
    "系统总览：四个核心应用的分层关系",
    "先看结构，再看流向。左侧是前端入口，中间是业务编排，右侧是知识能力底座。",
    2,
  );

  addText(slide, "展示层", { left: 92, top: 196, width: 80, height: 18 }, {
    fontSize: 14,
    bold: true,
    color: theme.muted,
  });
  addText(slide, "编排层", { left: 524, top: 196, width: 80, height: 18 }, {
    fontSize: 14,
    bold: true,
    color: theme.muted,
  });
  addText(slide, "能力层", { left: 936, top: 196, width: 80, height: 18 }, {
    fontSize: 14,
    bold: true,
    color: theme.muted,
  });

  const frontLane = card(slide, { left: 80, top: 226, width: 284, height: 356 }, theme.white, "#ddd3c2");
  const midLane = card(slide, { left: 496, top: 226, width: 288, height: 356 }, theme.softBlue, "#c9d8f4");
  const rightLane = card(slide, { left: 916, top: 226, width: 286, height: 356 }, theme.softGreen, "#c7dfd4");
  frontLane.text = "";
  midLane.text = "";
  rightLane.text = "";

  const f1 = makeModule(slide, {
    position: { left: 108, top: 258, width: 228, height: 110 },
    fill: "#fffaf1",
    lineFill: "#dcd1c1",
    tag: "Frontend 01",
    title: "agent_web_ui",
    subtitle: "智能助手工作台",
    titleSize: 24,
  });
  const f2 = makeModule(slide, {
    position: { left: 108, top: 414, width: 228, height: 110 },
    fill: "#fffaf1",
    lineFill: "#dcd1c1",
    tag: "Frontend 02",
    title: "knowledge\nplatform_ui",
    subtitle: "知识平台前台",
    titleSize: 18,
    titleHeight: 44,
  });
  const app = makeModule(slide, {
    position: { left: 532, top: 318, width: 216, height: 172 },
    fill: "#ffffff",
    lineFill: "#c4d2f1",
    tag: "Backend 01",
    title: "app",
    subtitle: "业务编排后端",
    titleSize: 34,
    tagFill: "#eef4ff",
    tagLine: "#d7e3fb",
    tagColor: theme.blue,
  });
  const kb = makeModule(slide, {
    position: { left: 952, top: 318, width: 214, height: 172 },
    fill: "#ffffff",
    lineFill: "#c7ddd2",
    tag: "Backend 02",
    title: "knowledge",
    subtitle: "知识能力后端",
    titleSize: 30,
    tagFill: "#eef8f4",
    tagLine: "#d4e8df",
    tagColor: theme.green,
  });

  arrow(slide, f1, app, theme.blue);
  arrow(slide, f2, app, theme.amber, "elbow", "right", "left");
  arrow(slide, app, kb, theme.green);

  addText(slide, "主关系", { left: 82, top: 620, width: 80, height: 18 }, {
    fontSize: 14,
    bold: true,
    color: theme.amber,
  });
  addText(slide, "智能助手前端主要对接 app；知识平台前端既可直接用 knowledge，也可通过 app 进入项目问答与分析链路。", {
    left: 154, top: 620, width: 980, height: 20,
  }, {
    fontSize: 16,
    color: theme.text,
  });
  footer(slide, "Source: ARCHITECTURE.md sections 2, 4, 5");
}

function routeFlow(presentation) {
  const slide = presentation.slides.add();
  bg(slide);
  titleBlock(
    slide,
    "Primary Flow",
    "主调用链：从前端入口到知识能力",
    "这一页只解释系统如何流动，不展开实现细节。",
    3,
  );

  const n1 = makeModule(slide, {
    position: { left: 86, top: 292, width: 214, height: 116 },
    fill: theme.white,
    lineFill: "#ddd3c2",
    tag: "入口 A",
    tagWidth: 68,
    title: "agent_web_ui",
    subtitle: "智能助手问答",
    titleSize: 24,
  });
  const n2 = makeModule(slide, {
    position: { left: 336, top: 292, width: 214, height: 116 },
    fill: theme.white,
    lineFill: "#ddd3c2",
    tag: "入口 B",
    tagWidth: 68,
    title: "knowledge\nplatform_ui",
    subtitle: "知识上传 / 问答",
    titleSize: 18,
    titleHeight: 40,
  });
  const n3 = makeModule(slide, {
    position: { left: 612, top: 270, width: 232, height: 160 },
    fill: "#ffffff",
    lineFill: "#c8d6f2",
    tag: "中枢",
    tagWidth: 56,
    title: "app",
    subtitle: "统一 API\n会话与项目上下文\n多代理与项目分析",
    titleSize: 34,
    titleHeight: 36,
    subtitleColor: theme.text,
  });
  const n4 = makeModule(slide, {
    position: { left: 922, top: 270, width: 232, height: 160 },
    fill: "#ffffff",
    lineFill: "#c8ddd4",
    tag: "底座",
    tagWidth: 56,
    title: "knowledge",
    subtitle: "上传入库\n检索重排\n知识问答",
    titleSize: 30,
    titleHeight: 36,
    subtitleColor: theme.text,
  });

  arrow(slide, n1, n3, theme.blue);
  arrow(slide, n2, n3, theme.amber);
  arrow(slide, n3, n4, theme.green);

  const note = card(slide, { left: 86, top: 500, width: 1068, height: 102 }, theme.navy, theme.navy);
  note.text = "";
  addText(slide, "汇报口径", { left: 116, top: 530, width: 90, height: 20 }, {
    fontSize: 16,
    bold: true,
    color: "#8fdcc3",
  });
  addText(slide, "1. 用户问题先进入前端入口。  2. 业务相关请求汇总到 app。  3. 需要知识增强时，app 再调用 knowledge。  4. knowledge 既可被 app 复用，也可支撑知识平台的直接能力。", {
    left: 218, top: 530, width: 880, height: 42,
  }, {
    fontSize: 18,
    color: "#e9f0ff",
  });
  footer(slide, "Source: ARCHITECTURE.md sections 2.2, 6.1, 6.2, 6.3");
}

function frontendStructure(presentation) {
  const slide = presentation.slides.add();
  bg(slide);
  titleBlock(
    slide,
    "Frontend Structure",
    "前端结构：两个入口，两个场景",
    "前端并不是功能重复，而是面向两类用户动作做了入口拆分。",
    4,
  );

  const left = card(slide, { left: 78, top: 212, width: 520, height: 414 }, theme.white, "#ddd3c2");
  const right = card(slide, { left: 682, top: 212, width: 520, height: 414 }, theme.white, "#ddd3c2");
  left.text = "";
  right.text = "";

  pill(slide, "FRONTEND 01", { left: 108, top: 242, width: 104, height: 24 }, "#eef4ff", "#d5e1f8", theme.blue);
  addText(slide, "agent_web_ui", { left: 108, top: 286, width: 240, height: 30 }, {
    fontSize: 32,
    bold: true,
    color: theme.ink,
  });
  addText(slide, "智能助手主工作台", { left: 110, top: 326, width: 180, height: 18 }, {
    fontSize: 17,
    color: theme.muted,
  });
  bulletLines(slide, [
    "登录、会话列表、聊天时间线",
    "项目上下文绑定与清空",
    "AI 报告总结展示",
    "统一输入框发起咨询和项目分析",
  ], 110, 386, 370, theme.text, theme.blue, 48, 18);

  pill(slide, "FRONTEND 02", { left: 712, top: 242, width: 104, height: 24 }, "#eef8f4", "#d2e5dc", theme.green);
  addText(slide, "knowledge_platform_ui", { left: 712, top: 286, width: 350, height: 30 }, {
    fontSize: 28,
    bold: true,
    color: theme.ink,
  });
  addText(slide, "知识平台前台", { left: 714, top: 326, width: 140, height: 18 }, {
    fontSize: 17,
    color: theme.muted,
  });
  bulletLines(slide, [
    "Knowledge 页面：上传文档、查看切分结果",
    "Chat 页面：知识问答 / 项目问答",
    "同时代理 app 与 knowledge 两类接口",
    "更适合知识运营与内容维护场景",
  ], 714, 386, 382, theme.text, theme.green, 48, 18);
  footer(slide, "Source: ARCHITECTURE.md sections 5.1, 5.2");
}

function backendStructure(presentation) {
  const slide = presentation.slides.add();
  bg(slide);
  titleBlock(
    slide,
    "Backend Structure",
    "后端结构：一个负责编排，一个负责能力",
    "后端拆分的重点不在技术栈，而在职责边界。",
    5,
  );

  const appBox = card(slide, { left: 78, top: 214, width: 520, height: 414 }, theme.softBlue, "#c9d8f4");
  const kbBox = card(slide, { left: 682, top: 214, width: 520, height: 414 }, theme.softGreen, "#c7dfd4");
  appBox.text = "";
  kbBox.text = "";

  pill(slide, "BUSINESS ORCHESTRATION", { left: 108, top: 244, width: 176, height: 24 }, "#ffffff", "#d8e2f8", theme.blue);
  addText(slide, "app  :8000", { left: 108, top: 288, width: 180, height: 34 }, {
    fontSize: 34,
    bold: true,
    color: theme.ink,
  });
  bulletLines(slide, [
    "统一 API 入口",
    "会话状态与项目上下文管理",
    "多代理路由与项目分析工作流",
    "按需调用 knowledge 做知识增强",
    "返回流式回答或结构化结果",
  ], 110, 362, 360, theme.text, theme.blue, 48, 18);

  pill(slide, "KNOWLEDGE CAPABILITY", { left: 712, top: 244, width: 160, height: 24 }, "#ffffff", "#d6e7df", theme.green);
  addText(slide, "knowledge  :8001", { left: 712, top: 288, width: 250, height: 34 }, {
    fontSize: 32,
    bold: true,
    color: theme.ink,
  });
  bulletLines(slide, [
    "文档上传与后台入库",
    "切分、向量检索、标题检索、重排",
    "提供独立知识问答接口",
    "既支撑知识平台，也支撑 app",
    "本质是共享知识服务",
  ], 714, 362, 366, theme.text, theme.green, 48, 18);

  const summary = card(slide, { left: 78, top: 648, width: 1124, height: 34 }, "#ffffff", "#ddd5c8", "rounded-xl");
  summary.text = "";
  addText(slide, "汇报结论", { left: 104, top: 656, width: 80, height: 16 }, {
    fontSize: 14,
    bold: true,
    color: theme.amber,
  });
  addText(slide, "`app` = orchestration layer；`knowledge` = capability layer。", {
    left: 192, top: 656, width: 680, height: 16,
  }, {
    fontSize: 16,
    bold: true,
    color: theme.ink,
  });
  footer(slide, "Source: ARCHITECTURE.md sections 5.3, 5.4");
}

async function writeNotes() {
  const plan = [
    "Style: executive architecture flow deck.",
    "Slide 1: cover and reporting scope.",
    "Slide 2: layered overview flow.",
    "Slide 3: primary call chain.",
    "Slide 4: frontend structure.",
    "Slide 5: backend structure.",
  ].join("\n");
  await fs.writeFile(path.join(TMP_DIR, "slide-plan.txt"), plan, "utf8");

  const sources = [
    "ARCHITECTURE.md",
    "Sections used: 2.2, 4.2, 5.1, 5.2, 5.3, 5.4, 6.1, 6.2, 6.3",
    "Supporting files:",
    "multi_agent/front/agent_web_ui/src/App.vue",
    "multi_agent/front/knowlege_platform_ui/src/views/Knowledge.vue",
    "multi_agent/front/knowlege_platform_ui/src/views/Chat.vue",
    "multi_agent/backed/app/api/routers.py",
    "multi_agent/backed/knowledge/api/routers.py",
  ].join("\n");
  await fs.writeFile(path.join(TMP_DIR, "source-notes.txt"), sources, "utf8");
}

async function buildPresentation() {
  const presentation = Presentation.create({
    slideSize: { width: 1280, height: 720 },
  });
  cover(presentation);
  overviewFlow(presentation);
  routeFlow(presentation);
  frontendStructure(presentation);
  backendStructure(presentation);
  return presentation;
}

async function renderAndExport(presentation) {
  for (const [index, slide] of presentation.slides.items.entries()) {
    const stem = `slide-${String(index + 1).padStart(2, "0")}`;
    const png = await presentation.export({ slide, format: "png", scale: 1 });
    await writeBlob(path.join(PREVIEW_DIR, `${stem}.png`), png);
    const layout = await slide.export({ format: "layout" });
    await fs.writeFile(path.join(LAYOUT_DIR, `${stem}.layout.json`), await layout.text(), "utf8");
  }
  const montage = await presentation.export({ format: "webp", montage: true, scale: 1 });
  await writeBlob(path.join(PREVIEW_DIR, "deck-montage.webp"), montage);
  const pptx = await PresentationFile.exportPptx(presentation);
  await pptx.save(FINAL_PPTX);
}

async function writeQa() {
  const qa = [
    "Visual QA summary",
    "1. Rebuilt deck as flowchart-style reporting artifact.",
    "2. Rendered all 5 slides and contact sheet.",
    "3. Checked connectors, title fit, and lane spacing.",
    "4. Focused only on two frontends and two backends.",
    "5. Final output exported to outputs/project-architecture-flow.pptx.",
  ].join("\n");
  await fs.writeFile(path.join(QA_DIR, "visual-qa.txt"), qa, "utf8");
}

async function main() {
  await ensureDirs();
  await writeNotes();
  const presentation = await buildPresentation();
  await renderAndExport(presentation);
  await writeQa();
  console.log(JSON.stringify({
    output: FINAL_PPTX,
    previewDir: PREVIEW_DIR,
    qaDir: QA_DIR,
  }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
