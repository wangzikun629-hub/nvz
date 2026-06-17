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
const { FileBlob, PresentationFile } = await import(
  pathToFileURL(artifactToolPath).href,
);

const SOURCE_PPTX =
  process.env.SOURCE_PPTX ||
  "C:/Users/ASUS/Desktop/新建 Microsoft PowerPoint 演示文稿.pptx";
const ROOT = "D:/nvz/kefu";
const THREAD_ID = "manual-20260615-user-ppt-extension";
const TASK_SLUG = "user-ppt-architecture-report-v2";
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
const FINAL_PPTX = path.join(OUTPUT_DIR, "user-ppt-architecture-report-v2.pptx");

const color = {
  white: "#ffffff",
  line: "#4c79d8",
  paleBlue: "#b9c7e3",
  palePink: "#e8aaaa",
  palePurple: "#cdb7de",
  paleGray: "#c5cad4",
  text: "#111111",
  softText: "#333333",
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

function addText(slide, text, position, style = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position,
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = {
    typeface: "Arial",
    fontSize: 22,
    color: color.text,
    ...style,
  };
  return shape;
}

function addBox(slide, position, fill, rounded = false) {
  return slide.shapes.add({
    geometry: rounded ? "roundRect" : "rect",
    position,
    fill,
    line: { style: "solid", fill: "none", width: 0 },
    ...(rounded ? { borderRadius: "rounded-3xl" } : {}),
  });
}

function addPill(slide, position, fill) {
  return slide.shapes.add({
    geometry: "roundRect",
    position,
    fill,
    line: { style: "solid", fill: "none", width: 0 },
    borderRadius: "rounded-full",
  });
}

function addArrow(
  slide,
  fromShape,
  toShape,
  fromSide = "right",
  toSide = "left",
  kind = "straight",
) {
  const connector = slide.shapes.connect(fromShape, toShape, {
    kind,
    fromSide,
    toSide,
    line: { style: "solid", fill: color.line, width: 1.4 },
    head: { type: "none", width: "sm", length: "sm" },
    tail: { type: "triangle", width: "sm", length: "sm" },
  });
  connector.bringToFront();
  return connector;
}

function addTitle(slide, title, subtitle) {
  addText(
    slide,
    title,
    { left: 72, top: 28, width: 520, height: 30 },
    { fontSize: 22, bold: true },
  );
  if (subtitle) {
    addText(
      slide,
      subtitle,
      { left: 72, top: 60, width: 680, height: 18 },
      { fontSize: 14, color: color.softText },
    );
  }
}

function addCenterTitle(slide, text, position, fontSize = 22) {
  addText(slide, text, position, {
    fontSize,
    bold: true,
    alignment: "center",
  });
}

function addCenterDesc(slide, text, position, fontSize = 14) {
  addText(slide, text, position, {
    fontSize,
    color: color.softText,
    alignment: "center",
  });
}

function addLabel(slide, text, position) {
  addText(slide, text, position, {
    fontSize: 15,
    color: color.softText,
    alignment: "center",
  });
}

function addBulletCard(slide, title, lines, position, fill) {
  addBox(slide, position, fill, true);
  addText(
    slide,
    title,
    { left: position.left + 18, top: position.top + 16, width: position.width - 36, height: 24 },
    { fontSize: 20, bold: true },
  );
  addText(
    slide,
    lines.join("\n"),
    { left: position.left + 18, top: position.top + 48, width: position.width - 36, height: position.height - 58 },
    { fontSize: 14, color: color.softText },
  );
}

function buildArchitectureSlide(slide) {
  slide.background.fill = color.white;
  addTitle(slide, "真实系统架构：两个前端、两个后端", "按代码实现整理，已去掉不准确的 Chroma 表述，当前知识库底座为 Milvus。");

  const frontA = addBox(slide, { left: 60, top: 250, width: 210, height: 112 }, color.paleBlue);
  const frontB = addBox(slide, { left: 60, top: 96, width: 210, height: 92 }, color.palePurple);
  const app = addBox(slide, { left: 350, top: 250, width: 230, height: 112 }, color.paleBlue);
  const knowledge = addBox(slide, { left: 670, top: 250, width: 230, height: 112 }, color.palePurple);
  const milvus = addBox(slide, { left: 985, top: 250, width: 170, height: 112 }, color.paleGray);
  const tools = addBox(slide, { left: 350, top: 470, width: 230, height: 94 }, color.paleBlue);

  addCenterTitle(slide, "知识库管理台", { left: 92, top: 118, width: 146, height: 24 }, 20);
  addCenterDesc(slide, "Vue 3 + Vite\n资料上传、分块预览、知识问答", { left: 90, top: 144, width: 150, height: 36 });

  addCenterTitle(slide, "智能助手工作台", { left: 88, top: 278, width: 154, height: 24 }, 22);
  addCenterDesc(slide, "Vue 3 + Element Plus\n统一提问入口、会话记录、项目绑定", { left: 88, top: 308, width: 154, height: 40 });

  addCenterTitle(slide, "业务编排后端", { left: 406, top: 278, width: 118, height: 24 }, 22);
  addCenterDesc(slide, "FastAPI + OpenAI Agents SDK + LangChain\n负责路由、会话状态、项目分析、结果汇总", { left: 380, top: 308, width: 170, height: 40 });

  addCenterTitle(slide, "知识库后端", { left: 730, top: 278, width: 110, height: 24 }, 22);
  addCenterDesc(slide, "FastAPI + Embedding + Rerank\n负责文档入库、检索召回、生成知识回答", { left: 700, top: 308, width: 170, height: 40 });

  addCenterTitle(slide, "Milvus\n向量库", { left: 1022, top: 274, width: 96, height: 40 }, 20);
  addCenterDesc(slide, "存放知识分块和向量\n支撑相似检索", { left: 1010, top: 314, width: 120, height: 30 });

  addCenterTitle(slide, "外部工具 / 项目目录", { left: 378, top: 496, width: 174, height: 24 }, 20);
  addCenterDesc(slide, "读取项目文件、生成图表、补充业务工具能力", { left: 374, top: 526, width: 182, height: 20 });

  addArrow(slide, frontA, app);
  addArrow(slide, frontB, knowledge, "right", "top");
  addArrow(slide, app, knowledge);
  addArrow(slide, knowledge, milvus);
  addArrow(slide, app, tools, "bottom", "top");

  addLabel(slide, "统一提问", { left: 288, top: 286, width: 52, height: 18 });
  addLabel(slide, "需要知识时调用", { left: 594, top: 286, width: 64, height: 18 });
  addLabel(slide, "检索 / 入库", { left: 920, top: 286, width: 54, height: 18 });
  addLabel(slide, "上传资料", { left: 388, top: 170, width: 60, height: 18 });

  addText(
    slide,
    "一句话说明：助手前端负责“问”，知识平台负责“管资料”，两个后端把“问答、项目分析、知识检索”串成一条链。",
    { left: 72, top: 624, width: 1080, height: 22 },
    { fontSize: 16, color: color.softText },
  );
}

function buildWorkflowSlide(slide) {
  slide.background.fill = color.white;
  addTitle(slide, "关键工作流：系统如何把问题答出来", "真实链路分成两条：一条处理普通知识问答，一条进入项目分析。");

  const user = addPill(slide, { left: 70, top: 260, width: 160, height: 86 }, color.palePink);
  const center = addBox(slide, { left: 280, top: 250, width: 230, height: 106 }, color.paleBlue, true);
  const branchA = addBox(slide, { left: 610, top: 160, width: 230, height: 96 }, color.palePurple, true);
  const branchB = addBox(slide, { left: 610, top: 360, width: 230, height: 116 }, color.paleBlue, true);
  const out = addBox(slide, { left: 940, top: 250, width: 220, height: 106 }, color.paleGray, true);

  addCenterTitle(slide, "用户提问", { left: 100, top: 288, width: 100, height: 24 }, 24);
  addCenterTitle(slide, "业务后端先判断\n这是什么问题", { left: 320, top: 278, width: 150, height: 42 }, 22);
  addCenterTitle(slide, "普通问答链路", { left: 670, top: 188, width: 110, height: 24 }, 22);
  addCenterDesc(slide, "直接调用知识库\n返回资料型答案", { left: 668, top: 218, width: 114, height: 30 });
  addCenterTitle(slide, "项目分析链路", { left: 670, top: 392, width: 110, height: 24 }, 22);
  addCenterDesc(slide, "识别项目 -> 读证据文件\n必要时再补知识库说明", { left: 652, top: 422, width: 146, height: 36 });
  addCenterTitle(slide, "输出给前端", { left: 995, top: 280, width: 110, height: 24 }, 22);
  addCenterDesc(slide, "流式过程提示\n最终结论 / 图表 / 报告", { left: 980, top: 310, width: 140, height: 34 });

  addArrow(slide, user, center);
  addArrow(slide, center, branchA, "right", "left", "elbow");
  addArrow(slide, center, branchB, "right", "left", "elbow");
  addArrow(slide, branchA, out, "right", "left");
  addArrow(slide, branchB, out, "right", "left");

  addLabel(slide, "问题路由", { left: 236, top: 286, width: 40, height: 18 });
  addLabel(slide, "一般咨询", { left: 540, top: 208, width: 50, height: 18 });
  addLabel(slide, "项目问题", { left: 540, top: 418, width: 50, height: 18 });
  addLabel(slide, "统一回复", { left: 870, top: 286, width: 52, height: 18 });

  addBulletCard(
    slide,
    "真实技术栈",
    [
      "前端：Vue 3、Vite、Element Plus",
      "业务后端：FastAPI、OpenAI Agents SDK、LangChain",
      "知识后端：Embedding、Milvus、外部 Rerank",
    ],
    { left: 72, top: 500, width: 450, height: 100 },
    color.paleBlue,
  );
  addBulletCard(
    slide,
    "对业务的价值",
    [
      "一个入口承接知识问答和项目追问",
      "项目一旦锁定，后续追问无需重复说明背景",
      "后台过程可见，便于客服和分析人员理解系统在做什么",
    ],
    { left: 590, top: 500, width: 560, height: 100 },
    color.palePurple,
  );
}

function buildProjectAnalysisSlide(slide) {
  slide.background.fill = color.white;
  addTitle(slide, "项目分析功能：不是直接回答，而是分步骤分析", "这一页只讲真实流程，帮助非技术同事理解系统为什么能给出更像分析员的答案。");

  const step1 = addBox(slide, { left: 70, top: 180, width: 190, height: 110 }, color.palePink, true);
  const step2 = addBox(slide, { left: 300, top: 180, width: 210, height: 110 }, color.paleBlue, true);
  const step3 = addBox(slide, { left: 555, top: 180, width: 220, height: 110 }, color.paleBlue, true);
  const step4 = addBox(slide, { left: 820, top: 180, width: 190, height: 110 }, color.palePurple, true);
  const step5 = addBox(slide, { left: 1040, top: 180, width: 150, height: 110 }, color.paleGray, true);

  addCenterTitle(slide, "1 识别项目", { left: 110, top: 206, width: 110, height: 24 }, 22);
  addCenterDesc(slide, "看用户是否点名项目\n或沿用当前会话已绑定项目", { left: 98, top: 238, width: 134, height: 34 });

  addCenterTitle(slide, "2 制定分析计划", { left: 348, top: 206, width: 114, height: 24 }, 22);
  addCenterDesc(slide, "先判断在问什么\n再决定优先看哪些指标和文件", { left: 330, top: 238, width: 150, height: 34 });

  addCenterTitle(slide, "3 读取项目证据", { left: 610, top: 206, width: 110, height: 24 }, 22);
  addCenterDesc(slide, "重点读取 samplelist、config、\nQC、比对、FRiP、peak、相关性文件", { left: 574, top: 238, width: 182, height: 36 });

  addCenterTitle(slide, "4 补知识与校验", { left: 854, top: 206, width: 122, height: 24 }, 22);
  addCenterDesc(slide, "必要时补查知识库\n并做事实校验、答案质量校验", { left: 840, top: 238, width: 150, height: 34 });

  addCenterTitle(slide, "5 输出结论", { left: 1072, top: 206, width: 86, height: 24 }, 22);
  addCenterDesc(slide, "形成可读结论\n并沉淀项目记忆", { left: 1056, top: 238, width: 118, height: 34 });

  addArrow(slide, step1, step2);
  addArrow(slide, step2, step3);
  addArrow(slide, step3, step4);
  addArrow(slide, step4, step5);

  addBulletCard(
    slide,
    "系统实际会优先读什么",
    [
      "项目基础信息：samplelist、config.yaml、报告摘要",
      "关键证据文件：ReadsQC、AlignmentQC、FRiP、peak、相关性表",
      "如果问题是画图或跨项目对比，会走单独分支",
    ],
    { left: 82, top: 370, width: 500, height: 132 },
    color.paleBlue,
  );
  addBulletCard(
    slide,
    "为什么它比普通问答更像“分析员”",
    [
      "不是只看一句问题，而是会去翻项目目录中的真实结果文件",
      "不是只看单个指标，而是会把上下游指标串起来看",
      "最后还有答案质量和事实校验，降低一本正经说错的风险",
    ],
    { left: 646, top: 370, width: 520, height: 132 },
    color.palePurple,
  );

  addText(
    slide,
    "一句话说明：项目分析能力的核心价值，不是“会聊天”，而是“能按项目证据一步步做判断”。",
    { left: 72, top: 620, width: 1080, height: 22 },
    { fontSize: 16, color: color.softText },
  );
}

function buildPainPointSlide(slide) {
  slide.background.fill = color.white;
  addTitle(slide, "当前痛点与后续优化方向", "以下内容按现有代码实现归纳，适合在汇报里讲“为什么还需要继续建设”。");

  addBulletCard(
    slide,
    "痛点 1：业务后端过重",
    [
      "项目分析已经拆成大量服务模块，维护门槛较高",
      "功能很强，但新人理解和排错成本也在上升",
    ],
    { left: 72, top: 130, width: 330, height: 118 },
    color.paleBlue,
  );
  addBulletCard(
    slide,
    "痛点 2：状态依赖本地目录",
    [
      "会话、项目绑定、项目记忆都落在本地文件目录",
      "单机开发方便，但多实例部署和统一运维会更复杂",
    ],
    { left: 438, top: 130, width: 330, height: 118 },
    color.paleBlue,
  );
  addBulletCard(
    slide,
    "痛点 3：知识入库任务偏轻量",
    [
      "上传任务状态保存在进程内存里，重启后不易追踪",
      "适合当前规模，但不利于更大批量资料治理",
    ],
    { left: 804, top: 130, width: 330, height: 118 },
    color.palePurple,
  );
  addBulletCard(
    slide,
    "痛点 4：链路依赖较多",
    [
      "项目定位要扫描目录，知识检索还依赖外部 rerank",
      "问题复杂时，整体响应速度和稳定性更受外部条件影响",
    ],
    { left: 72, top: 286, width: 330, height: 118 },
    color.palePurple,
  );

  addBulletCard(
    slide,
    "建议的优化方向",
    [
      "把会话状态、上传任务、项目记忆逐步服务化",
      "给项目分析链路做更清晰的分层和监控",
      "把知识入库与检索能力做成更标准的中台能力",
    ],
    { left: 438, top: 286, width: 696, height: 118 },
    color.paleGray,
  );

  const left = addBox(slide, { left: 180, top: 470, width: 220, height: 86 }, color.palePink, true);
  const middle = addBox(slide, { left: 490, top: 470, width: 220, height: 86 }, color.paleBlue, true);
  const right = addBox(slide, { left: 800, top: 470, width: 220, height: 86 }, color.palePurple, true);
  addCenterTitle(slide, "当前价值", { left: 240, top: 492, width: 100, height: 24 }, 22);
  addCenterDesc(slide, "已能跑通问答、知识库、\n项目分析三类核心场景", { left: 220, top: 520, width: 140, height: 28 });
  addCenterTitle(slide, "当前瓶颈", { left: 550, top: 492, width: 100, height: 24 }, 22);
  addCenterDesc(slide, "复杂度开始上升\n需要工程化治理", { left: 530, top: 520, width: 140, height: 28 });
  addCenterTitle(slide, "下一阶段目标", { left: 846, top: 492, width: 128, height: 24 }, 22);
  addCenterDesc(slide, "从“能用”走向“稳定、可扩展、可运营”", { left: 822, top: 520, width: 176, height: 28 });
  addArrow(slide, left, middle);
  addArrow(slide, middle, right);
}

async function main() {
  await ensureDirs();

  await fs.writeFile(
    path.join(TMP_DIR, "slide-plan.txt"),
    [
      "Mode: targeted extension based on user-provided PPT style.",
      "Audience: reporting audience including non-technical stakeholders.",
      "Append 4 slides: actual architecture, key workflow, project analysis detail, pain points.",
      "Facts verified from code: Vue 3/Vite frontends, FastAPI backends, Milvus vector store, project-analysis workflow.",
    ].join("\n"),
    "utf8",
  );

  await fs.writeFile(
    path.join(TMP_DIR, "source-notes.txt"),
    [
      `Template source: ${SOURCE_PPTX}`,
      "Code facts used:",
      "D:/nvz/kefu/main.py",
      "D:/nvz/kefu/requirements.txt",
      "D:/nvz/kefu/multi_agent/front/agent_web_ui/package.json",
      "D:/nvz/kefu/multi_agent/front/knowlege_platform_ui/package.json",
      "D:/nvz/kefu/multi_agent/backed/app/api/routers.py",
      "D:/nvz/kefu/multi_agent/backed/app/services/business_agent/runtime_service.py",
      "D:/nvz/kefu/multi_agent/backed/app/services/project_analysis_service.py",
      "D:/nvz/kefu/multi_agent/backed/knowledge/api/routers.py",
      "D:/nvz/kefu/multi_agent/backed/knowledge/repositories/vector_store_repository.py",
      "D:/nvz/kefu/multi_agent/backed/knowledge/services/retrieval_service.py",
    ].join("\n"),
    "utf8",
  );

  const presentation = await PresentationFile.importPptx(
    await FileBlob.load(SOURCE_PPTX),
  );

  const s1 = presentation.slides.add();
  const s2 = presentation.slides.add();
  const s3 = presentation.slides.add();
  const s4 = presentation.slides.add();

  buildArchitectureSlide(s1);
  buildWorkflowSlide(s2);
  buildProjectAnalysisSlide(s3);
  buildPainPointSlide(s4);

  for (const [index, slide] of presentation.slides.items.entries()) {
    const stem = `slide-${String(index + 1).padStart(2, "0")}`;
    const png = await presentation.export({ slide, format: "png", scale: 1 });
    await writeBlob(path.join(PREVIEW_DIR, `${stem}.png`), png);

    const layout = await slide.export({ format: "layout" });
    await fs.writeFile(
      path.join(LAYOUT_DIR, `${stem}.layout.json`),
      await layout.text(),
      "utf8",
    );
  }

  const montage = await presentation.export({
    format: "webp",
    montage: true,
    scale: 1,
  });
  await writeBlob(path.join(PREVIEW_DIR, "deck-montage.webp"), montage);

  const pptx = await PresentationFile.exportPptx(presentation);
  await pptx.save(FINAL_PPTX);

  await fs.writeFile(
    path.join(QA_DIR, "visual-qa.txt"),
    [
      "Visual QA summary",
      "1. Imported the original user deck and preserved all existing slides.",
      "2. Appended 4 report-friendly slides based on actual code facts.",
      "3. Corrected the vector-store statement to Milvus.",
      "4. Added one dedicated slide explaining the project-analysis workflow.",
      `5. Final output: ${FINAL_PPTX}`,
    ].join("\n"),
    "utf8",
  );

  console.log(
    JSON.stringify(
      {
        output: FINAL_PPTX,
        previewDir: PREVIEW_DIR,
        qaDir: QA_DIR,
        slideCount: presentation.slides.items.length,
      },
      null,
      2,
    ),
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
