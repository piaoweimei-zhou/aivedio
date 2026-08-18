const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageNumber, LevelFormat, PageBreak
} = require("docx");

// ======== 读取并解析 MD ========
const md = fs.readFileSync("高考方案.md", "utf-8");

// ======== 辅助函数 ========
const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };
const cellMargins = { top: 60, bottom: 60, left: 100, right: 100 };

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 300, after: 200 },
    children: [new TextRun({ text, bold: true, size: 32, font: "微软雅黑" })],
  });
}
function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 240, after: 160 },
    children: [new TextRun({ text, bold: true, size: 28, font: "微软雅黑" })],
  });
}
function h3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 200, after: 120 },
    children: [new TextRun({ text, bold: true, size: 24, font: "微软雅黑" })],
  });
}
function para(text, opts = {}) {
  const runs = [];
  if (typeof text === "string") {
    runs.push(new TextRun({ text, size: 21, font: "宋体", ...opts }));
  } else if (Array.isArray(text)) {
    text.forEach(t => {
      if (typeof t === "string") runs.push(new TextRun({ text: t, size: 21, font: "宋体" }));
      else runs.push(new TextRun({ size: 21, font: "宋体", ...t }));
    });
  }
  return new Paragraph({
    spacing: { after: 100, line: 360 },
    children: runs,
  });
}
function bullet(text, level = 0) {
  return new Paragraph({
    numbering: { reference: "bullets", level },
    spacing: { after: 60, line: 340 },
    children: [new TextRun({ text, size: 21, font: "宋体" })],
  });
}

function makeTable(headers, rows) {
  const colW = Math.floor(9000 / headers.length);
  return new Table({
    width: { size: 9000, type: WidthType.DXA },
    columnWidths: headers.map(() => colW),
    rows: [
      new TableRow({
        children: headers.map(h => new TableCell({
          borders, width: { size: colW, type: WidthType.DXA },
          shading: { fill: "2E5E8E", type: ShadingType.CLEAR },
          margins: cellMargins,
          children: [new Paragraph({ children: [new TextRun({ text: h, bold: true, color: "FFFFFF", size: 21, font: "微软雅黑" })] })],
        })),
      }),
      ...rows.map(row => new TableRow({
        children: row.map((cell, i) => new TableCell({
          borders, width: { size: colW, type: WidthType.DXA },
          shading: i === 0 ? { fill: "E8F0FE", type: ShadingType.CLEAR } : undefined,
          margins: cellMargins,
          children: [new Paragraph({ children: [new TextRun({ text: cell, size: 20, font: "宋体" })] })],
        })),
      })),
    ],
  });
}

// ======== 构建文档 ========
const children = [];

// 标题页
children.push(new Paragraph({ spacing: { before: 3000 }, children: [] }));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 200 },
  children: [new TextRun({ text: "陕西物理类 542 分 / 位次 38044", bold: true, size: 44, font: "微软雅黑", color: "1F4E79" })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 100 },
  children: [new TextRun({ text: "（物 + 化 + 地）完整志愿规划方案", bold: true, size: 36, font: "微软雅黑", color: "2E75B6" })],
}));
children.push(new Paragraph({ spacing: { after: 600 }, children: [] }));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 100 },
  children: [new TextRun({ text: "—— 基于国家十五五政策 · 经济周期 · 个人优势三维匹配", size: 24, font: "微软雅黑", color: "666666" })],
}));
children.push(new PageBreak());

// 目录
children.push(h1("总目录"));
const tocItems = [
  "一、考生基础画像与核心约束条件",
  "二、顶层规划逻辑：三维匹配体系",
  "三、五大优选赛道深度论证",
  "四、冲/稳/保分层院校筛选完整理由",
  "五、45 条完整可直接填报志愿清单",
  "六、长期学业与就业配套规划",
  "七、填报风险防控细则",
];
tocItems.forEach(item => children.push(para([{ text: item, bold: true }])));
children.push(new PageBreak());

// 一、考生基础画像
children.push(h1("一、考生基础画像与核心约束条件"));
children.push(h2("1. 成绩与选科"));
children.push(para("总分 542，全省物理类位次 38044；选科物理 + 化学 + 地理"));
children.push(para("单科：语文 113、外语 108（文理均衡）、化学 84、地理 81（两大优势学科）、物理 65（明显短板）"));

children.push(h2("2. 硬性约束"));
[
  "规避重度物理课程（电磁场、电力系统、理论力学、量子物理为主的专业），降低挂科、学习吃力风险",
  "优先适配化学、地理高分，最大化大学学习优势、专业课高分利于评优、考研",
  "核心目标：长期稳定就业、4 年后薪酬可持续、贴合国家产业政策、穿越经济周期",
  "地域双路线：可留陕西本地央企/编制，也可出省进长三角、北方新能源、数字产业集群",
  "陕西本科批 45 个院校专业组，必须填满，全部服从专业调剂，杜绝滑档",
].forEach(t => bullet(t));

children.push(h2("3. 修正前期四大偏差"));
[
  "放弃杭州电子科技大学（往年陕西录取位次 16000 以内），替换重庆邮电大学（ICT 赛道、位次匹配）",
  "薪资全部下调至陕西本地真实综合年薪，区分一线/二三线薪资差异",
  "严格区分专业物理负荷：仅保留低物理的网安、大数据、GIS、化工、环境、师范",
  "师范定位修正：普通师范≠带编，仅公费/优师计划保障编制",
].forEach(t => bullet(t));

// 二、顶层规划逻辑
children.push(h1("二、顶层规划逻辑：三维匹配体系"));

children.push(h2("维度 1：十五五国家核心扶持产业"));
children.push(para("依据国家十五五规划纲要，锁定四大万亿级赛道："));
[
  "新型能源体系 + 绿色低碳：风光储、氢能、锂电新材料、污染治理、碳核查",
  "数字中国 + 信创网络安全：算力、数据安全、智慧城市 GIS，人才缺口 300 万+",
  "现代化交通基建 + 国土空间规划：高铁、轨道交通、城乡规划、自然资源测绘",
  "国防军工新材料 + 民生基础教育：国防预算逐年上涨、县域教师扩招",
].forEach(t => bullet(t));
children.push(para([{ text: "淘汰赛道：", bold: true }, { text: "纯传统金融、纯法学、老式机械、无资源的纯农学、普通临床医学、纯土木施工" }]));

children.push(h2("维度 2：经济发展规律筛选标准"));
[
  "优先国企占比 60% 以上行业：抗经济下行，无大规模裁员",
  "赛道分层：长青稳定赛道（化工/环境/电力/师范）＞高薪弹性赛道（网安/GIS）",
  "行业生命周期：处于成长扩张期（新能源、信创），拒绝衰退饱和行业",
  "学历增值空间：化工、材料、环境考研薪资提升 40% 以上",
].forEach(t => bullet(t));

children.push(h2("维度 3：考生个人优势精准匹配"));
[
  "化学 84 分：应用化学、高分子、能源化工、环境工程，专业课以化学为主",
  "地理 81 分：地理科学师范、GIS、城乡规划，考公规划院岗位充足",
  "语文外语均衡：大数据管理、师范、经管数字财务有优势",
  "物理 65 分短板：避开电气工程、自动化、飞行器、纯光电等重物理专业",
].forEach(t => bullet(t));

// 三、五大优选赛道
children.push(h1("三、五大优选赛道深度论证"));

const赛道数据 = [
  ["绿色化工/新材料/环境工程", "极低", "13-22 万", "隆基、陕煤、中石油、环保设计院", "化学核心，最适配"],
  ["GIS 地理信息/国土规划", "低", "15-20 万", "自然资源厅、规划院、高德/百度地图", "地理高分专属赛道"],
  ["网络空间安全/大数据管理", "中等", "20-28 万", "华为、深信服、网信办、公安网安", "高薪弹性赛道"],
  ["电力/轨道交通交通类", "中等", "14-21 万", "国家电网、大唐发电、铁路局", "极致稳定福利"],
  ["化学/地理师范", "极低", "10-18 万", "各地中小学、教育局", "零失业民生赛道"],
];
var tableData = [
  ["绿色化工/新材料/环境工程", "极低", "13-22 万", "隆基、陕煤、中石油、环保设计院", "化学核心，最适配"],
  ["GIS 地理信息/国土规划", "低", "15-20 万", "自然资源厅、规划院、高德/百度地图", "地理高分专属赛道"],
  ["网络空间安全/大数据管理", "中等", "20-28 万", "华为、深信服、网信办、公安网安", "高薪弹性赛道"],
  ["电力/轨道交通交通类", "中等", "14-21 万", "国家电网、大唐发电、铁路局", "极致稳定福利"],
  ["化学/地理师范", "极低", "10-18 万", "各地中小学、教育局", "零失业民生赛道"],
];
children.push(makeTable(["赛道", "物理负荷", "4年年薪(陕西)", "就业渠道", "评价"], tableData));
children.push(new Paragraph({ spacing: { after: 200 }, children: [] }));

// 四、冲稳保分层院校
children.push(h1("四、冲/稳/保分层院校筛选完整理由"));

children.push(h2("（一）冲刺层（1-6 志愿，位次 32000-37500）"));
const冲刺 = [
  ["陕西师范大学", "211 西安", "化学/地理师范", "12-18 万"],
  ["长安大学", "211 西安", "GIS/土地资源管理", "15-20 万"],
  ["太原理工大学", "211 山西", "应用化学/环境工程", "14-19 万"],
  ["重庆邮电大学", "行业强校", "网络空间安全/大数据", "20-30 万"],
  ["西安邮电大学", "本地IT强校", "网安/大数据", "20-28 万"],
  ["合肥工业大学", "211 安徽", "能源化学/应用化学", "15-22 万"],
];
var sprintData = [
  ["陕西师范大学", "211 西安", "化学/地理师范", "12-18 万"],
  ["长安大学", "211 西安", "GIS/土地资源管理", "15-20 万"],
  ["太原理工大学", "211 山西", "应用化学/环境工程", "14-19 万"],
  ["重庆邮电大学", "行业强校", "网络空间安全/大数据", "20-30 万"],
  ["西安邮电大学", "本地IT强校", "网安/大数据", "20-28 万"],
  ["合肥工业大学", "211 安徽", "能源化学/应用化学", "15-22 万"],
];
children.push(makeTable(["院校", "层次", "首选专业", "4年年薪"], sprintData));

children.push(h2("（二）稳妥层（7-28 志愿，位次 37500-45000）"));
const稳妥 = [
  ["西安建筑科技大学", "环境工程/GIS", "14-20 万"],
  ["西安理工大学", "环境工程/应用化学", "14-19 万"],
  ["陕西科技大学", "轻化工程/高分子", "13-19 万"],
  ["西安工程大学", "环境工程/地理空间信息", "13-18 万"],
  ["西安石油大学", "化学工程/能源化学", "14-19 万"],
  ["西安工业大学", "材料化学/环境工程", "13-18 万"],
  ["长沙理工大学", "电厂化学/环境工程", "15-21 万"],
  ["南京工业大学", "应用化学/高分子", "15-22 万"],
  ["青岛科技大学", "高分子/储能工程", "14-20 万"],
  ["东北电力大学", "电厂化学/环境工程", "14-20 万"],
  ["桂林电子科技大学", "大数据/网络安全", "18-26 万"],
  ["北京信息科技大学", "大数据/信息管理", "19-27 万"],
];
var stableData = [
  ["西安建筑科技大学", "环境工程/GIS", "14-20 万"],
  ["西安理工大学", "环境工程/应用化学", "14-19 万"],
  ["陕西科技大学", "轻化工程/高分子", "13-19 万"],
  ["西安工程大学", "环境工程/地理空间信息", "13-18 万"],
  ["西安石油大学", "化学工程/能源化学", "14-19 万"],
  ["西安工业大学", "材料化学/环境工程", "13-18 万"],
  ["长沙理工大学", "电厂化学/环境工程", "15-21 万"],
  ["南京工业大学", "应用化学/高分子", "15-22 万"],
  ["青岛科技大学", "高分子/储能工程", "14-20 万"],
  ["东北电力大学", "电厂化学/环境工程", "14-20 万"],
  ["桂林电子科技大学", "大数据/网络安全", "18-26 万"],
  ["北京信息科技大学", "大数据/信息管理", "19-27 万"],
];
children.push(makeTable(["院校", "首选专业", "4年年薪"], stableData));

children.push(h2("（三）保底层（29-45 志愿，位次 45000+）"));
children.push(para("陕西本地公办兜底：宝鸡文理学院、咸阳师范学院、西安文理学院、陕西理工大学、渭南师范学院、商洛学院（年薪 9-15 万）"));
children.push(para("省外行业保底：中北大学、兰州理工、石家庄铁道、兰州交大、西华大学、成都大学、天津财大、山东师大、浙江财大、青岛大学、济南大学（年薪 11-21 万）"));

// 五、志愿清单
children.push(h1("五、45 条完整可直接复制志愿清单"));
children.push(para([{ text: "全部勾选服从专业调剂；", bold: true }, { text: "同校专业顺序：化学/环境/GIS/网安放最前，避开重物理专业" }]));

children.push(h3("1-6 冲刺段（211/行业头部）"));
const冲清单 = [
  ["陕西师范大学", "化学(师范)、地理科学(师范)", "基础教育", "12-18 万"],
  ["长安大学(211)", "地理信息科学、土地资源管理", "国土交通", "15-20 万"],
  ["太原理工大学(211)", "应用化学、环境工程", "绿色化工", "14-19 万"],
  ["重庆邮电大学", "网络空间安全、大数据管理", "数字信创", "20-30 万"],
  ["西安邮电大学", "网络空间安全、数据科学", "西北ICT", "20-28 万"],
  ["合肥工业大学(211)", "应用化学、能源化学工程", "储能新材料", "15-22 万"],
];
var sprintList = [
  ["陕西师范大学", "化学(师范)、地理科学(师范)", "基础教育", "12-18 万"],
  ["长安大学(211)", "地理信息科学、土地资源管理", "国土交通", "15-20 万"],
  ["太原理工大学(211)", "应用化学、环境工程", "绿色化工", "14-19 万"],
  ["重庆邮电大学", "网络空间安全、大数据管理", "数字信创", "20-30 万"],
  ["西安邮电大学", "网络空间安全、数据科学", "西北ICT", "20-28 万"],
  ["合肥工业大学(211)", "应用化学、能源化学工程", "储能新材料", "15-22 万"],
];
children.push(makeTable(["院校", "首选专业", "赛道", "4年年薪"], sprintList));

children.push(h3("7-28 稳妥主力段"));
children.push(para("西安建大/西安理工/陕西科大/西安工程/西安石油/西安工业/长沙理工/南京工业/青岛科技/东北电力/长春理工/东北林大(211)/内蒙古大学(211)/山西大学(双一流)/扬州大学/桂电/湖北工业/北京信息科技/南京林业/中国民航/华东交大/天津理工"));

children.push(h3("29-45 保底公办段"));
children.push(para("宝鸡文理/咸阳师范/西安文理/陕西理工/渭南师范/商洛学院/中北大学/兰州理工/石家庄铁道/兰州交大/西华大学/成都大学/天津财大/山东师大/浙江财大/青岛大学/济南大学"));

// 六、长期规划
children.push(h1("六、长期学业与就业配套规划"));
children.push(h2("路线 1：稳定留陕体制/央企"));
["志愿调整：将省内稳妥、本地保底志愿顺序前移", "专业重心：GIS、环境工程、化学师范、能源化工"].forEach(t => bullet(t));
children.push(para([{ text: "在校规划：", bold: true }, { text: "师范路线大一开始备考教资；化工/GIS路线考取碳排放管理师、测绘证；大三准备考研" }]));

children.push(h2("路线 2：高薪出省产业集群"));
["志愿调整：重庆邮电、南京工业、青岛科技、桂电前置", "在校规划：自学 Python、数据库、安全运维，大二实习"].forEach(t => bullet(t));
children.push(para("一线 4 年年薪可达 25-35 万；长三角新材料、西南数字产业、山东锂电橡胶集群"));

children.push(h2("路线 3：211 学历考研/选调生"));
["志愿重心：冲刺层 6 所 211、东北林大、内大、山西大学双一流"].forEach(t => bullet(t));
children.push(para("211/双一流学历是省考定向选调硬性门槛"));

// 七、风险防控
children.push(h1("七、填报风险防控细则"));
children.push(h2("退档风险防控"));
bullet("全部院校勾选服从专业调剂");
bullet("提前查阅招生章程，色盲色弱限制专业剔除");
children.push(h2("学习挂科风险防控"));
bullet("坚决不填报高物理专业，仅保留低物理细分方向");
bullet("优先化学、地理相关专业，利用高分优势拉高绩点");
children.push(h2("就业预期风险防控"));
bullet("薪资为综合年收入（含公积金折算），纯到手现金再降 20%");
bullet("网安民企高薪但存在 35 岁职业分化，需持续技术学习");
children.push(h2("梯度滑档防控"));
bullet("冲稳保比例：6 冲 + 22 稳 + 17 保，梯度拉开");
bullet("45 个志愿全部填满，不空缺任何位置");

// ======== 生成 DOCX ========
const doc = new Document({
  styles: {
    default: { document: { run: { font: "宋体", size: 21 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "微软雅黑", color: "1F4E79" },
        paragraph: { spacing: { before: 300, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, font: "微软雅黑", color: "2E75B6" },
        paragraph: { spacing: { before: 240, after: 160 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: "微软雅黑" },
        paragraph: { spacing: { before: 200, after: 120 }, outlineLevel: 2 } },
    ],
  },
  numbering: {
    config: [{
      reference: "bullets",
      levels: [{ level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } } }],
    }],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          alignment: AlignmentType.RIGHT,
          children: [new TextRun({ text: "陕西物理类 542 分志愿规划方案", size: 18, font: "微软雅黑", color: "999999" })],
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "— ", size: 18, color: "999999" }), new TextRun({ children: [PageNumber.CURRENT], size: 18, color: "999999" }), new TextRun({ text: " —", size: 18, color: "999999" })],
        })],
      }),
    },
    children,
  }],
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync("高考方案.docx", buffer);
  console.log("✅ 已生成: 高考方案.docx");
});
