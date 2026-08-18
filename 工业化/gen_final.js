const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageNumber, LevelFormat, PageBreak
} = require("docx");

const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };
const cellMargins = { top: 60, bottom: 60, left: 100, right: 100 };
const h1 = text => new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 300, after: 200 }, children: [new TextRun({ text, bold: true, size: 32, font: "微软雅黑", color: "1F4E79" })] });
const h2 = text => new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 240, after: 160 }, children: [new TextRun({ text, bold: true, size: 28, font: "微软雅黑", color: "2E75B6" })] });
const h3 = text => new Paragraph({ heading: HeadingLevel.HEADING_3, spacing: { before: 200, after: 120 }, children: [new TextRun({ text, bold: true, size: 24, font: "微软雅黑" })] });
const h4 = text => new Paragraph({ spacing: { before: 160, after: 100 }, children: [new TextRun({ text, bold: true, size: 22, font: "微软雅黑", color: "333333" })] });
function para(text) {
  const runs = typeof text === "string" ? [{ text, size: 21, font: "宋体" }] : text;
  return new Paragraph({ spacing: { after: 100, line: 360 }, children: runs.map(r => new TextRun({ size: 21, font: "宋体", ...r })) });
}
function bullet(text) {
  return new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60, line: 340 }, children: [new TextRun({ text, size: 21, font: "宋体" })] });
}
function makeTable(headers, rows, colWidths) {
  if (!colWidths) { const cw = Math.floor(9000 / headers.length); colWidths = headers.map(() => cw); }
  return new Table({
    width: { size: 9000, type: WidthType.DXA }, columnWidths: colWidths,
    rows: [
      new TableRow({ children: headers.map((h, i) => new TableCell({ borders, width: { size: colWidths[i], type: WidthType.DXA }, shading: { fill: "1F4E79", type: ShadingType.CLEAR }, margins: cellMargins, children: [new Paragraph({ children: [new TextRun({ text: h, bold: true, color: "FFFFFF", size: 20, font: "微软雅黑" })] })] })) }),
      ...rows.map(row => new TableRow({ children: row.map((cell, i) => new TableCell({ borders, width: { size: colWidths[i], type: WidthType.DXA }, shading: i === 0 ? { fill: "F2F7FB", type: ShadingType.CLEAR } : undefined, margins: cellMargins, children: [new Paragraph({ children: [new TextRun({ text: String(cell), size: 19, font: "宋体" })] })] })) })),
    ],
  });
}

// ======== 赛道政策数据 ========
const trackInfo = {
  "绿色化工/新材料": { policy: "十五五绿色制造、储能、锂电、污染治理", demand: "高端化工人才缺口120万+", growth: "年增速15%", neg: "部分厂区偏远、四班三倒、需接触化学品" },
  "GIS/国土空间": { policy: "全国国土空间统一规划、智慧城市", demand: "自然资源系统年招2万+", growth: "稳定增长", neg: "项目期频繁加班画图、出差踏勘" },
  "网络空间安全/大数据": { policy: "数字中国、等保2.0、信创国产替代", demand: "人才缺口300万+", growth: "年增速25%", neg: "高强度加班、35岁转型压力、技术更新快" },
  "电力/轨道交通": { policy: "新型电力系统、全国高铁网", demand: "央企校招固定名额", growth: "稳定", neg: "倒班制（四班三倒）、节假日在岗、偏远厂区" },
  "民航/铁道": { policy: "航空强国、铁路网持续建设", demand: "民航年招1万+", growth: "稳定", neg: "轮班、节假日在岗、部分岗位需倒班" },
  "应急消防": { policy: "国家应急体系改革", demand: "行政编制定向招录", growth: "扩编中", neg: "准军事化管理、体能训练、火灾勘查需去火场" },
  "生物医药": { policy: "健康中国2030", demand: "稳定增长", growth: "稳定", neg: "实验室工作需长时间站立、接触化学试剂" },
};

const allVolunteers = [
  { stage: "提前批", order: "提前1", school: "中国消防救援学院", group: "消防工程组", level: "行业唯一", major: "消防工程", code: "083102K", score2025: "549", diff: "+7", track: "应急消防", salary: "14-19万", policy: trackInfo["应急消防"].policy, learn: "燃烧化学、建筑防火设计、消防给排水。化学是核心，物理要求低。准军事化管理", work: "在机关审核建筑消防图纸，防火监督检查，坐办公室为主。行政编制授四级指挥员衔", neg: trackInfo["应急消防"].neg, bargain: "★★★☆☆" },
  { stage: "提前批", order: "提前2", school: "中国消防救援学院", group: "火灾勘查组", level: "行业唯一", major: "火灾勘查", code: "083107TK", score2025: "512", diff: "-30", track: "应急消防", salary: "14-19万", policy: trackInfo["应急消防"].policy, learn: "火灾动力学、物证鉴定、化学分析。用化学方法反推起火原因", work: "去火场废墟勘查，通过烟熏痕迹、化学残留判断起火点，技术专家岗", neg: "需去火场废墟勘查，偶尔出外勤", bargain: "★★★★☆" },
  { stage: "提前批", order: "提前3", school: "武警工程大学(西安)", group: "武警管理组", level: "准军事化", major: "武警管理科学与工程/军队指挥", code: "110101", score2025: "525", diff: "-17", track: "应急消防", salary: "13-18万", policy: "国防武警体制改革", learn: "武警指挥、管理科学、应急处置，军事化管理。物理要求低，侧重管理和组织协调能力", work: "毕业后授武警中尉警衔，分配至各省武警总队，从事指挥管理或后勤保障工作", neg: "准军事化全封闭管理，体能训练强度大，需通过政审体检", bargain: "★★★★☆" },
  { stage: "提前批", order: "提前4", school: "大连海事大学", group: "海事管理组", level: "211", major: "海事管理/物流管理", code: "120408T", score2025: "530", diff: "-12", track: "民航/铁道", salary: "15-22万", policy: "海洋强国、一带一路", learn: "海事法规、航运管理、物流系统工程。文理兼收，物理要求极低，适合地理/化学优势学生", work: "海事局、港口集团、航运企业、船级社。办公室+偶尔港口巡查，稳定且行业壁垒高", neg: "部分课程需海上实习（约1-2个月），海事局岗位需通过公务员考试", bargain: "★★★★★" },
  { stage: "提前批", order: "提前5", school: "上海海事大学", group: "航运管理组", level: "行业特色", major: "交通管理(航运方向)/供应链管理", code: "120407T", score2025: "525", diff: "-17", track: "民航/铁道", salary: "16-24万", policy: "上海国际航运中心建设", learn: "航运经济、供应链管理、国际货运代理。偏管理和经济，物理要求极低", work: "航运企业、港口集团、跨国物流公司。上海陆家嘴/临港新片区，薪资有上海地域溢价", neg: "上海生活成本高，但行业薪资基数大", bargain: "★★★★★" },
  { stage: "提前批", order: "提前6", school: "中国民航大学", group: "民航提前批", level: "行业唯一", major: "飞行技术/空中交通管理", code: "081805K", score2025: "538", diff: "-4", track: "民航/铁道", salary: "18-30万", policy: trackInfo["民航/铁道"].policy, learn: "空中交通管制、航空气象、飞行运行管理。其中空管方向物理要求中等，飞行技术方向有身体要求", work: "民航局空管局、各大航空公司、机场运行中心。空管员在塔台工作，高端紧缺岗位", neg: "空管需通过ICAO英语四级+心理筛查；飞行技术对身体素质要求极高", bargain: "★★★★☆" },
  { stage: "冲刺", order: "1", school: "长安大学(211)", group: "低分地理组", level: "211", major: "地理信息科学/土地资源管理", code: "070504", score2025: "548", diff: "+6", track: "GIS/国土空间", salary: "15-20万", policy: trackInfo["GIS/国土空间"].policy, learn: "遥感、测绘、空间数据库、ArcGIS软件。地理高分直接优势，无需高中物理", work: "做电子地图、国土空间规划、数据分析。自然资源局、中交集团、高德百度地图", neg: trackInfo["GIS/国土空间"].neg, bargain: "★★☆☆☆", note: "高分王牌组（道路桥梁+6分）分数更高，本方案推荐低分GIS组" },
  { stage: "冲刺", order: "2", school: "太原理工大学(211)", group: "化工环境组", level: "211", major: "应用化学/环境工程", code: "070302", score2025: "545", diff: "+3", track: "绿色化工", salary: "14-19万", policy: trackInfo["绿色化工/新材料"].policy, learn: "四大化学+化工原理，物理极少。可偏储能材料或工业分析方向", work: "产品检测、工艺优化，实验室或中控室。新能源企业、化工央企", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★☆☆", note: "高分王牌组（电气+8分）分数更高，本方案推荐低分化工环境组" },
  { stage: "冲刺", order: "3", school: "西南石油大学(双一流)", group: "化工储能组", level: "双一流", major: "化学工程与工艺/新能源材料", code: "081301", score2025: "543", diff: "+1", track: "绿色化工", salary: "14-20万", policy: trackInfo["绿色化工/新材料"].policy, learn: "油气加工、化学反应工程，聚焦石油化工。锂离子电池、光伏材料", work: "炼化厂中控室监控生产参数，或研发部门做电池材料合成。中石油、中石化、宁德时代", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★☆", note: "" },
  { stage: "冲刺", order: "4", school: "重庆邮电大学", group: "网安数据组", level: "行业强校", major: "网络空间安全/大数据管理", code: "080911TK", score2025: "547", diff: "+5", track: "网络空间安全/大数据", salary: "20-30万", policy: trackInfo["网络空间安全/大数据"].policy, learn: "密码学、网络攻防、安全审计，编程为主，避开电磁场硬件课", work: "渗透测试、安全运维，办公室监控加固系统。网信办、公安网安、华为深信服", neg: trackInfo["网络空间安全/大数据"].neg, bargain: "★★☆☆☆", note: "高分王牌组（通信工程+8分）分数更高，本方案推荐网安数据低分方向" },
  { stage: "冲刺", order: "5", school: "合肥工业大学(211)", group: "化学储能组", level: "211", major: "应用化学/能源化学工程", code: "070302", score2025: "536", diff: "-6", track: "绿色化工", salary: "15-22万", policy: trackInfo["绿色化工/新材料"].policy, learn: "侧重储能材料方向。清洁煤、生物质能、氢能", work: "长三角新能源企业，考电网可报化学类专业岗。能源央企研究院", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★☆", note: "" },
  { stage: "冲刺", order: "6", school: "西安邮电大学", group: "大数据组", level: "本地IT强校", major: "数据科学与大数据技术/大数据管理", code: "080910T", score2025: "533", diff: "-9", track: "网络空间安全/大数据", salary: "20-28万", policy: trackInfo["网络空间安全/大数据"].policy, learn: "Python、数据库、机器学习，偏技术应用或管理方向", work: "数据处理、模型搭建。西安软件园、三大运营商", neg: trackInfo["网络空间安全/大数据"].neg, bargain: "★★★★★", note: "⚠️ 高分王牌组（通信工程552分+10）独立分组！本方案推荐533大数据组，勿错填通信组" },
  { stage: "稳妥", order: "7", school: "西安建筑科技大学", group: "环境GIS组", level: "建筑老八校", major: "环境工程/给排水/GIS", code: "082502", score2025: "525", diff: "-17", track: "绿色化工", salary: "14-20万", policy: trackInfo["绿色化工/新材料"].policy, learn: "依托土木优势，偏建筑给排水和城市水处理", work: "西北各大设计院、中建体系，设计岗画图为主", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★☆", note: "高分王牌组（建筑学+5分）分数更高，本方案推荐低分环境GIS组" },
  { stage: "稳妥", order: "8", school: "西安理工大学", group: "环境化工组", level: "省属工科龙头", major: "环境工程/应用化学", code: "082502", score2025: "522", diff: "-20", track: "绿色化工", salary: "14-19万", policy: trackInfo["绿色化工/新材料"].policy, learn: "依托水利特色，偏水环境治理。电力化学、水处理药剂方向", work: "各省水利设计院环保科室", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★☆", note: "" },
  { stage: "稳妥", order: "9", school: "陕西科技大学", group: "轻工化工组", level: "西部轻工唯一", major: "轻化工程/高分子材料", code: "081701", score2025: "525", diff: "-17", track: "绿色化工", salary: "13-19万", policy: trackInfo["绿色化工/新材料"].policy, learn: "造纸、皮革、纺织化学，西部唯一特色，化学核心", work: "产品工艺配方，如纸张添加剂、洗涤剂配方。中粮、恒安、日化企业", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★☆", note: "" },
  { stage: "稳妥", order: "10", school: "西安石油大学", group: "化工组", level: "行业特色", major: "化学工程/能源化学工程", code: "081301", score2025: "531", diff: "-11", track: "绿色化工", salary: "14-19万", policy: trackInfo["绿色化工/新材料"].policy, learn: "油气加工方向，化工原理+油田化学", work: "中石油长庆油田、陕西燃气，能源化工基地", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★☆", note: "" },
  { stage: "稳妥", order: "11", school: "西安工业大学(兵工七子)", group: "材料化工组", level: "兵工七子", major: "材料化学/环境工程", code: "080403", score2025: "534", diff: "-8", track: "绿色化工", salary: "13-18万", policy: "国防预算逐年上涨", learn: "军用新材料，隐身涂层、装甲材料，化学合成", work: "西安本地兵器研究所、航天院所，实验室研发涉密稳定", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★☆", note: "" },
  { stage: "稳妥", order: "12", school: "西安工程大学", group: "环境化工组", level: "行业特色", major: "环境工程/地理空间信息", code: "082502", score2025: "518", diff: "-24", track: "绿色化工", salary: "13-18万", policy: trackInfo["绿色化工/新材料"].policy, learn: "纺织印染废水处理，与化学结合", work: "本地环保企业、纺织厂环保科", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★★", note: "" },
  { stage: "稳妥", order: "13", school: "西安财经大学", group: "大数据组", level: "财经特色", major: "大数据财务管理/统计学", code: "120204T", score2025: "523", diff: "-19", track: "网络空间安全/大数据", salary: "14-20万", policy: "数字经济", learn: "财务分析+Python数据分析，文理结合", work: "银行、国企财务部，用数据工具做财务预测", neg: "办公室工作，稳定但薪资涨幅有限", bargain: "★★★★★", note: "" },
  { stage: "稳妥", order: "14", school: "陕西中医药大学", group: "药学组", level: "医药特色", major: "药学/中药学/医学检验", code: "100701", score2025: "520", diff: "-22", track: "生物医药", salary: "13-19万", policy: trackInfo["生物医药"].policy, learn: "药理学、药剂学、药物分析，化学衍生", work: "医院药剂科、药企质检，配药做药品质量检测", neg: trackInfo["生物医药"].neg, bargain: "★★★★★", note: "" },
  { stage: "稳妥", order: "15", school: "中国民航大学", group: "民航组", level: "行业唯一", major: "民航安全工程/油气储运", code: "082901", score2025: "538", diff: "-4", track: "民航/铁道", salary: "15-21万", policy: trackInfo["民航/铁道"].policy, learn: "机场安全管理、民航法规、风险评估", work: "各大机场安全管理部门，央企稳定", neg: trackInfo["民航/铁道"].neg, bargain: "★★★☆☆", note: "" },
  { stage: "稳妥", order: "16", school: "长沙理工大学", group: "电力化工组", level: "电力名校", major: "电厂化学/环境工程", code: "070302", score2025: "539", diff: "-3", track: "电力/轨道交通", salary: "15-21万", policy: trackInfo["电力/轨道交通"].policy, learn: "电厂水处理、腐蚀与防护", work: "发电厂化学车间做水样化验，需倒班但福利极好", neg: trackInfo["电力/轨道交通"].neg, bargain: "★★★☆☆", note: "" },
  { stage: "稳妥", order: "17", school: "东北电力大学", group: "电力化工组", level: "电力名校", major: "电厂化学/环境工程", code: "070302", score2025: "530", diff: "-12", track: "电力/轨道交通", salary: "14-20万", policy: trackInfo["电力/轨道交通"].policy, learn: "同上，全国电网定点校招", work: "同上", neg: trackInfo["电力/轨道交通"].neg, bargain: "★★★★☆", note: "" },
  { stage: "稳妥", order: "18", school: "青岛科技大学", group: "橡胶化工组", level: "橡胶黄埔", major: "高分子材料/储能工程", code: "080407", score2025: "535", diff: "-7", track: "绿色化工", salary: "14-20万", policy: trackInfo["绿色化工/新材料"].policy, learn: "橡胶方向，被誉为'中国橡胶工业的黄埔'", work: "橡胶配方研发，密炼车间和实验室", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★☆☆", note: "" },
  { stage: "稳妥", order: "19", school: "华东交通大学", group: "铁道运输组", level: "铁道特色", major: "交通运输/交通工程(GIS)", code: "081801", score2025: "527", diff: "-15", track: "电力/轨道交通", salary: "14-19万", policy: trackInfo["电力/轨道交通"].policy, learn: "铁路行车组织、调度指挥、站场设计", work: "铁路局运输处、地铁运营公司，行车调度或车站值班员", neg: trackInfo["电力/轨道交通"].neg, bargain: "★★★★☆", note: "" },
  { stage: "稳妥", order: "20", school: "兰州交通大学", group: "铁道化工组", level: "铁道特色", major: "给排水/化学工程", code: "081003", score2025: "526", diff: "-16", track: "电力/轨道交通", salary: "13-18万", policy: trackInfo["电力/轨道交通"].policy, learn: "铁路给排水特色、防腐材料方向", work: "铁路系统", neg: trackInfo["电力/轨道交通"].neg, bargain: "★★★★☆", note: "" },
  { stage: "稳妥", order: "21", school: "石家庄铁道大学", group: "工程管理组", level: "铁道特色", major: "工程管理/地理信息科学", code: "120103", score2025: "489", diff: "-53", track: "电力/轨道交通", salary: "13-18万", policy: trackInfo["电力/轨道交通"].policy, learn: "工程造价、施工组织，偏管理", work: "工程预算、标书，坐办公室随项目流动", neg: trackInfo["电力/轨道交通"].neg, bargain: "★★★★★", note: "" },
  { stage: "稳妥", order: "22", school: "东北林业大学(211)", group: "GIS组", level: "211", major: "地理信息科学/环境科学", code: "070504", score2025: "539", diff: "-3", track: "GIS/国土空间", salary: "13-18万", policy: trackInfo["GIS/国土空间"].policy, learn: "林业GIS方向", work: "自然资源局、林草局，211考公有优势", neg: trackInfo["GIS/国土空间"].neg, bargain: "★★★☆☆", note: "" },
  { stage: "稳妥", order: "23", school: "内蒙古大学(211)", group: "生态组", level: "211", major: "生态学/环境工程", code: "071004", score2025: "537", diff: "-5", track: "GIS/国土空间", salary: "13-18万", policy: "生态文明建设", learn: "生态修复、环境监测，适合选调生路线", work: "生态环境局、自然资源局，考公岗位对口", neg: "基层事业单位薪资偏低", bargain: "★★★☆☆", note: "" },
  { stage: "稳妥", order: "24", school: "山西大学(双一流)", group: "环境化工组", level: "双一流", major: "环境工程/应用化学", code: "082502", score2025: "528", diff: "-14", track: "绿色化工", salary: "12-18万", policy: trackInfo["绿色化工/新材料"].policy, learn: "基础扎实，有保研名额", work: "环保企业、事业单位", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★☆", note: "" },
  { stage: "稳妥", order: "25", school: "扬州大学", group: "环境组", level: "综合强校", major: "环境工程/资源环境科学", code: "082502", score2025: "532", diff: "-10", track: "绿色化工", salary: "14-19万", policy: trackInfo["绿色化工/新材料"].policy, learn: "长三角地区环保产业发达", work: "长三角环保企业、事业单位", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★☆", note: "" },
  { stage: "稳妥", order: "26", school: "北京信息科技大学", group: "大数据组", level: "北京IT特色", major: "大数据管理与应用", code: "120108T", score2025: "536", diff: "-6", track: "网络空间安全/大数据", salary: "19-27万", policy: trackInfo["网络空间安全/大数据"].policy, learn: "同前述大数据专业", work: "北京政企数字化部门", neg: trackInfo["网络空间安全/大数据"].neg, bargain: "★★★☆☆", note: "北京生活成本高，但薪资基数大" },
  { stage: "稳妥", order: "27", school: "长春理工大学", group: "材料化工组", level: "光电特色", major: "材料化学/化学工程", code: "080403", score2025: "530", diff: "-12", track: "绿色化工", salary: "13-19万", policy: "国防军工", learn: "偏光学材料、光电功能材料", work: "军工光电企业，西安有大量对口研究所", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★☆", note: "" },
  { stage: "稳妥", order: "28", school: "沈阳药科大学", group: "药学组", level: "药界清华", major: "药学/药物化学", code: "100701", score2025: "534", diff: "-8", track: "生物医药", salary: "14-20万", policy: trackInfo["生物医药"].policy, learn: "新药分子设计合成，纯有机化学，全国顶尖药科", work: "实验室合成化合物、药理测试，药企核心研发岗", neg: trackInfo["生物医药"].neg, bargain: "★★★★☆", note: "" },
  { stage: "稳妥", order: "29", school: "天津理工大学", group: "材料化工组", level: "地方强校", major: "材料化学/环境生态", code: "080403", score2025: "523", diff: "-19", track: "绿色化工", salary: "13-18万", policy: trackInfo["绿色化工/新材料"].policy, learn: "京津冀地区新材料产业", work: "新材料企业技术岗", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★★", note: "" },
  { stage: "稳妥", order: "30", school: "南京工业大学", group: "环境组", level: "化工强校", major: "环境工程", code: "082502", score2025: "550", diff: "+8", track: "绿色化工", salary: "15-22万", policy: trackInfo["绿色化工/新材料"].policy, learn: "强在工业水处理，化工园区污水处理", work: "化工园区、环保企业", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★☆☆☆", note: "分数超542，仅作冲刺参考" },
  { stage: "保底", order: "31", school: "西安科技大学", group: "化工安全组", level: "省属", major: "化学工程/安全工程", code: "081301", score2025: "504", diff: "-38", track: "绿色化工", salary: "12-17万", policy: trackInfo["绿色化工/新材料"].policy, learn: "煤化工方向，对口陕煤集团", work: "陕煤集团、能源企业技术岗", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★★", note: "" },
  { stage: "保底", order: "32", school: "西安文理学院", group: "化工环境组", level: "市属", major: "化学工程/环境生态", code: "081301", score2025: "498", diff: "-44", track: "绿色化工", salary: "10-14万", policy: trackInfo["绿色化工/新材料"].policy, learn: "本地应用型", work: "西安本地化工、环保企业", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★★", note: "" },
  { stage: "保底", order: "33", school: "陕西理工大学", group: "化工组", level: "省属", major: "应用化学/环境工程", code: "070302", score2025: "502", diff: "-40", track: "绿色化工", salary: "11-16万", policy: trackInfo["绿色化工/新材料"].policy, learn: "陕南地区，对口本地企业和教育系统", work: "陕南化工企业、环保单位", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★★", note: "" },
  { stage: "保底", order: "34", school: "延安大学", group: "化工组", level: "省属", major: "化学工程/资源环境", code: "081301", score2025: "510", diff: "-32", track: "绿色化工", salary: "11-16万", policy: trackInfo["绿色化工/新材料"].policy, learn: "陕北能源化工基地", work: "陕北能源企业", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★★", note: "" },
  { stage: "保底", order: "35", school: "商洛学院", group: "环境组", level: "市属", major: "资源环境科学", code: "082506T", score2025: "475", diff: "-67", track: "绿色化工", salary: "9-14万", policy: trackInfo["绿色化工/新材料"].policy, learn: "基层生态监测、事业单位方向", work: "基层环保、自然资源事业单位", neg: "基层岗位薪资较低", bargain: "★★★★★", note: "" },
  { stage: "保底", order: "36", school: "渭南师范学院", group: "环境组", level: "市属", major: "环境生态工程", code: "082504", score2025: "493", diff: "-49", track: "绿色化工", salary: "10-14万", policy: trackInfo["绿色化工/新材料"].policy, learn: "非师范方向，可考事业单位", work: "环保检测机构、事业单位", neg: "基层岗位", bargain: "★★★★★", note: "" },
  { stage: "保底", order: "37", school: "榆林学院", group: "能源化工组", level: "市属", major: "能源化学工程", code: "081304T", score2025: "490", diff: "-52", track: "绿色化工", salary: "10-15万", policy: trackInfo["绿色化工/新材料"].policy, learn: "榆林国家级能源基地", work: "中煤、陕煤，本地就业强", neg: "榆林地处陕北，生活条件较艰苦", bargain: "★★★★★", note: "榆林补贴高，综合年薪实际+2-3万" },
  { stage: "保底", order: "38", school: "安康学院", group: "环境组", level: "市属", major: "资源环境科学", code: "082506T", score2025: "472", diff: "-70", track: "绿色化工", salary: "9-13万", policy: "南水北调生态", learn: "南水北调水源地保护方向", work: "环保岗位、基层事业单位", neg: "基层岗位", bargain: "★★★★★", note: "" },
  { stage: "保底", order: "39", school: "中北大学(兵工七子)", group: "军工化工组", level: "兵工七子", major: "应用化学/复合材料", code: "070302", score2025: "515", diff: "-27", track: "绿色化工", salary: "13-18万", policy: "国防军工", learn: "军工炸药、推进剂方向", work: "就业兵工集团，稳定", neg: "涉密项目，部分岗位地处偏远", bargain: "★★★★★", note: "" },
  { stage: "保底", order: "40", school: "兰州理工大学", group: "化工组", level: "省属", major: "应用化学/环境工程", code: "070302", score2025: "502", diff: "-40", track: "绿色化工", salary: "12-17万", policy: trackInfo["绿色化工/新材料"].policy, learn: "西部能源化工方向", work: "西北能源企业", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★★", note: "" },
  { stage: "保底", order: "41", school: "西华大学", group: "材料化工组", level: "省属", major: "材料化学/环境工程", code: "080403", score2025: "513", diff: "-29", track: "绿色化工", salary: "12-17万", policy: trackInfo["绿色化工/新材料"].policy, learn: "成渝制造业方向", work: "成渝地区制造企业", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★★", note: "" },
  { stage: "保底", order: "42", school: "成都大学", group: "药学组", level: "市属", major: "药学/环境生态", code: "100701", score2025: "511", diff: "-31", track: "生物医药", salary: "11-16万", policy: trackInfo["生物医药"].policy, learn: "西南医药产业方向", work: "成都生物医药企业", neg: trackInfo["生物医药"].neg, bargain: "★★★★★", note: "" },
  { stage: "保底", order: "43", school: "青岛大学", group: "药学环境组", level: "省属", major: "药学/环境工程", code: "100701", score2025: "516", diff: "-26", track: "生物医药", salary: "13-19万", policy: trackInfo["生物医药"].policy, learn: "山东是医药大省", work: "山东医药企业、环保单位", neg: trackInfo["生物医药"].neg, bargain: "★★★★★", note: "" },
  { stage: "保底", order: "44", school: "济南大学", group: "化工组", level: "省属", major: "应用化学/材料科学", code: "070302", score2025: "514", diff: "-28", track: "绿色化工", salary: "13-19万", policy: trackInfo["绿色化工/新材料"].policy, learn: "山东化工集群", work: "山东化工企业", neg: trackInfo["绿色化工/新材料"].neg, bargain: "★★★★★", note: "" },
  { stage: "保底", order: "45", school: "天津财经大学", group: "大数据组", level: "财经强校", major: "大数据管理与应用/统计学", code: "120108T", score2025: "523", diff: "-19", track: "网络空间安全/大数据", salary: "14-20万", policy: "数字经济", learn: "京津冀数字金融方向", work: "银行后台数据分析、金融科技", neg: trackInfo["网络空间安全/大数据"].neg, bargain: "★★★★★", note: "" },
];

// ======== 构建文档 ========
const children = [];

// ===== 封面 =====
children.push(new Paragraph({ spacing: { before: 3000 }, children: [] }));
children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 100 }, children: [new TextRun({ text: "2026年陕西高考志愿", bold: true, size: 48, font: "微软雅黑", color: "1F4E79" })] }));
children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 100 }, children: [new TextRun({ text: "终极填报方案（完整修复版）", bold: true, size: 36, font: "微软雅黑", color: "2E75B6" })] }));
children.push(new Paragraph({ spacing: { after: 200 }, children: [] }));
children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 80 }, children: [new TextRun({ text: "物理类 542分 · 位次 38044 · 选科 物理+化学+地理", size: 28, font: "微软雅黑", color: "333333" })] }));
children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 40 }, children: [new TextRun({ text: "核心策略：放弃师范 · 规避重度物理 · 主攻化工/环境/GIS/网安/民航/铁道/消防", size: 24, font: "微软雅黑", color: "E67E22", bold: true })] }));
children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 40 }, children: [new TextRun({ text: "⚠️ 本版已修复：专业组拆分 · 专项计划 · 投档规则 · 地域薪资 · 行业负面提示", size: 20, font: "微软雅黑", color: "C0392B" })] }));
children.push(new PageBreak());

// ===== 使用说明 =====
children.push(h1("使用说明"));
children.push(para("本方案为综合修复版，在原整合版基础上补充了以下六大模块："));
const fixItems = [
  ["① 专业组拆分", "区分同一学校的高分王牌组和低分化工/环境组，避免误填高分专业组滑档"],
  ["② 专项计划", "新增国家专项/地方专项报考条件+可填报院校+预估分差"],
  ["③ 投档规则", "2026陕西新高考院校专业组投档机制详解"],
  ["④ 地域薪资", "长三角/京津冀/一线城市/能源基地薪资上浮系数"],
  ["⑤ 考研数据", "各层次院校保研率+考研目标院校参考"],
  ["⑥ 行业负面提示", "化工倒班、铁道轮班、网安35岁转型等客观警示"],
];
children.push(makeTable(["修复项", "说明"], fixItems, [2500, 6500]));
children.push(new Paragraph({ spacing: { after: 100 }, children: [] }));
children.push(para([{ text: "特别提醒：", bold: true, color: "C0392B" }, { text: "本方案数据以2025年录取分为基准，2026年各校招生计划、专业组划分可能有变化。填报前务必登录陕西省教育考试院官网核对2026年《招生计划汇编》，以当年公布为准。" }]));
children.push(new PageBreak());

// ===== 第一部分：投档规则 =====
children.push(h1("重要：2026陕西投档规则说明（必读）"));
children.push(h2("一、院校专业组投档机制"));
children.push(para("陕西新高考实行「院校专业组」投档，各高校按选科要求和专业性质分为若干个「专业组」，每个专业组独立投档，有各自的投档分数线。"));
children.push(para([{ text: "关键规则：", bold: true }]));
children.push(bullet("同一学校不同专业组互不相通：你填报了A专业组，只能被该组内的专业录取"));
children.push(bullet("服从专业调剂仅在该组内生效：不会被调剂到该学校的其他专业组"));
children.push(bullet("典型案例：西安邮电大学大数据组533分 vs 通信工程组552分——两组相差19分，独立投档"));

children.push(h2("二、填报时必须做的三件事"));
children.push(bullet("查2026年招生计划：各校专业组代码、组内包含专业、选科要求每年可能有变化"));
children.push(bullet("核对专业组分数：本方案标注的分数是该组2025年投档线，参考价值大，但不等于2026年分数"));
children.push(bullet('看清组内是否有"天坑"专业：如果你报的组内包含电气工程、自动化等重物理专业且你无法接受，则需谨慎考虑是否填报该组'));

children.push(h2("三、本方案的组别标注说明"));
children.push(para("本方案已对部分同校多分段的院校做了组别拆分，标注方式如下："));
children.push(bullet("西安邮电大学 → 标注为「大数据组」（533分），另有高分王牌组「通信工程组」（552分）未纳入"));
children.push(bullet("长安大学 → 标注为「低分地理组」（548分），另有「道路桥梁王牌组」（555分+）未纳入"));
children.push(bullet("未拆分标注的院校：表示该校各专业组分数接近，或本方案推荐的组别即为该校最低分组"));
children.push(new PageBreak());

// ===== 第二部分：专项计划 =====
children.push(h1("重要：专项计划自查（符合条件的考生可降20-40分录取）"));
children.push(h2("一、你是否符合条件？"));
const specialPlan = [
  ["国家专项计划", "陕西56个贫困县户籍+连续3年当地高中学籍", "省内外一本院校", "普通批-20~40分", "降分幅度最大，强烈建议申请"],
  ["地方专项计划", "陕西所有农村户籍考生", "省属一本院校", "普通批-15~30分", "覆盖范围广，陕西农村考生均可"],
  ["高校专项计划", "农村户籍+成绩在一本线以上", "95所重点大学", "视学校而定", "需提前在阳光高考平台报名"],
];
children.push(makeTable(["类型", "报考条件", "可报院校", "降分幅度", "建议"], specialPlan, [1600, 2000, 2000, 1600, 1800]));

children.push(h2("二、专项计划推荐院校（从本方案清单中筛选）"));
children.push(para("若你符合专项条件，以下院校在专项批中录取分通常更低，强烈建议加入专项志愿："));
children.push(bullet("国家专项可报：长安大学(211)、合肥工业大学(211)、东北林业大学(211)、内蒙古大学(211) —— 专项批通常降20-30分"));
children.push(bullet("地方专项可报：西安建筑科技大学、西安理工大学、西安科技大学、陕西科技大学、延安大学 —— 专项批通常降15-25分"));
children.push(para([{ text: "填报策略：", bold: true }, { text: "符合条件的考生应先填专项批志愿（不占用普通批45个名额），再填普通批。专项批未被录取不影响普通批。" }]));
children.push(new PageBreak());

// ===== 第三部分：考生画像与策略 =====
children.push(h1("第一部分：考生画像与核心策略"));
children.push(h2("一、成绩与硬性约束"));
children.push(makeTable(["项目", "数据"], [["总分/位次", "542分 / 全省物理类 38044位"], ["选科", "物理+化学+地理"], ["化学/地理", "84分 / 81分（两大优势学科）"], ["物理/语文/外语", "65分（短板）/ 113分 / 108分"], ["核心约束", "① 规避重度物理 ② 适配化学地理高分 ③ 长期稳定就业 ④ 可留陕可出省 ⑤ 45志愿填满"]], [3000, 6000]));
children.push(h2("二、三维匹配逻辑"));
children.push(h3("维度1：国家十五五产业政策"));
children.push(makeTable(["赛道", "核心内容", "政策红利"], [["新型能源+绿色低碳", "风光储、氢能、锂电新材料", "双碳十年国策"], ["数字中国+信创安全", "算力、数据安全、智慧城市", "人才缺口300万+"], ["交通基建+国土规划", "高铁、轨道交通、城乡规划", "国土空间统一规划"], ["国防军工+民生教育", "军工新材料、基础教育", "国防预算逐年上涨"]], [2500, 4000, 2500]));
children.push(h3("维度2：考生个人优势"));
children.push(bullet("化学84分 → 应用化学、高分子、能源化工、药学（化学核心，物理极少）"));
children.push(bullet("地理81分 → GIS地理信息、城乡规划（考公规划院岗位充足）"));
children.push(bullet("物理65分规避 → 不报电气工程、自动化、飞行器、纯光电、硬件通信"));
children.push(new PageBreak());

// ===== 第四部分：提前批 =====
children.push(h1("第二部分：提前批方案"));
children.push(para([{ text: "提前批不占用本科45个志愿名额。", bold: true }, { text: " 以下为推荐填报的特色院校，全部为准军事化管理或行业唯一特色院校。" }]));
children.push(para([{ text: "关键提示：", bold: true, color: "C0392B" }, { text: "以下推荐的专业均已避开重度物理。军校类（武警工程）以管理指挥为主，海事类以航运管理/物流管理为主，均适合物理65分短板考生。" }]));
const advance = allVolunteers.filter(v => v.stage === "提前批");
children.push(makeTable(["志愿", "院校", "专业组", "专业", "2025分", "分差", "年薪", "捡漏"], advance.map(v => [v.order, v.school, v.group, v.major, v.score2025, v.diff, v.salary, v.bargain]), [700, 2400, 1400, 1200, 800, 700, 900, 800]));

// 按学校分类详细说明
children.push(h2("（一）应急消防类"));
children.push(para([{ text: "中国消防救援学院", bold: true }, { text: " — 行业唯一应急管理部直属，毕业即授消防救援衔（行政编制）。准军事化全封闭管理，穿制服每日体能训练。适合追求稳定体制内工作的男生。" }]));
children.push(h2("（二）武警/军校类"));
children.push(para([{ text: "武警工程大学（西安）", bold: true }, { text: " — 武警部队直属院校，位于西安本地。推荐管理科学与工程/军队指挥类专业，以管理为主物理要求低。毕业授武警中尉警衔，分配至各省武警总队。需通过政审体检。" }]));
children.push(para([{ text: "军校/武警报考条件：", bold: true }, { text: "未婚（年龄17-20周岁），政审合格，体检通过（视力要求：裸眼4.5以上/矫正4.9以上/未做过激光手术）。毕业包分配，授衔，服役期一般8-10年后可转业地方。" }]));
children.push(h2("（三）海事/航运类"));
children.push(para([{ text: "大连海事大学（211）", bold: true }, { text: " — 交通运输部直属211，航海家的摇篮。推荐海事管理/物流管理，偏管理方向物理要求极低。半军事化管理（穿制服出早操），毕业去向：海事局、港口集团、航运央企。211学历+行业壁垒，就业优势明显。" }]));
children.push(para([{ text: "上海海事大学", bold: true }, { text: " — 华东航运龙头，上海国际航运中心建设核心人才来源。推荐交通管理（航运方向）/供应链管理，偏经济管理物理要求极低。毕业去向：上海港务集团、跨国物流公司、航运金融企业。" }]));
children.push(h2("（四）民航类"));
children.push(para([{ text: "中国民航大学", bold: true }, { text: " — 民航局直属，中国民航人才的黄埔军校。推荐空中交通管理方向（非飞行技术，无需飞行员身体条件）。毕业后进入民航局空管局或各航空公司运行中心，塔台空管员属高端紧缺技术岗。" }]));
children.push(para([{ text: "填报顺序建议：", bold: true }, { text: "若坚定走体制内路线，建议将武警工程大学提前至前两位；若更看重行业薪资和地域发展，将海事/民航类前移。同一学校内优先推荐的管理类专业放最前面。" }]));
children.push(para([{ text: "⚠️ 所有提前批院校均需通过对应体检/政审/面试：", bold: true, color: "C0392B" }, { text: "消防需体测政审，武警/军校需军检政审，海事需海检（辨色力+听力），民航空管需IIIa级体检+英语四级。请在填报前完成各校官网要求的测试环节。" }]));
children.push(new PageBreak());

// ===== 第五部分：45志愿清单 =====
children.push(h1("第三部分：45个志愿完整清单（修复版）"));
children.push(para([{ text: "填报规则：", bold: true }, { text: "全部勾选服从专业调剂（仅组内生效）。优先化学/环境/GIS/大数据/交通管理类专业。避开电气工程、自动化、轮机、硬件通信。" }]));
children.push(para([{ text: "捡漏评级：", bold: true }, { text: "★越多性价比越高。★★★★★=强烈推荐，★★★★=推荐，★★★=中等，★★=冲刺" }]));
children.push(para([{ text: "组别说明：", bold: true, color: "C0392B" }, { text: " 已拆分同校不同分组，标注了各组投档分，请注意区分" }]));

for (const stage of ["冲刺", "稳妥", "保底"]) {
  const items = allVolunteers.filter(v => v.stage === stage);
  const stageLabel = stage === "冲刺" ? "冲刺段（1-6）" : stage === "稳妥" ? "稳妥段（7-30）" : "保底段（31-45）";
  children.push(h2(stageLabel));

  let prevSchool = "";
  for (const v of items) {
    if (v.school !== prevSchool) {
      const groupNote = v.note ? para([{ text: "⚠️ " + v.note, color: "C0392B", italics: true }]) : null;
      children.push(h3(`${v.order}. ${v.school}（${v.level}）— ${v.group}`));
      if (groupNote) children.push(groupNote);
      prevSchool = v.school;
    }
    const negText = v.neg ? ` | 注意：${v.neg}` : "";
    children.push(para([{ text: `【${v.track}】${v.major}（${v.code}）`, bold: true }, { text: `  | 2025年${v.score2025}分 · 分差${v.diff} · 年薪${v.salary} · 捡漏${v.bargain}` }]));
    children.push(para([{ text: "学什么：", bold: true, color: "2E75B6" }, { text: v.learn }]));
    children.push(para([{ text: "做什么：", bold: true, color: "2E75B6" }, { text: v.work + negText }]));
    children.push(new Paragraph({ spacing: { after: 100 }, children: [] }));
  }
}
children.push(new PageBreak());

// ===== 第六部分：捡漏分析 =====
children.push(h1("第四部分：捡漏分析与梯度策略"));
children.push(h2("一、五星捡漏院校（强烈推荐）"));
const topBargain = allVolunteers.filter(v => v.bargain === "★★★★★" && v.stage !== "提前批");
children.push(makeTable(["志愿", "院校", "专业组", "分差", "年薪", "赛道"], topBargain.map(v => [v.order, v.school, v.group, v.diff, v.salary, v.track]), [800, 2400, 1500, 700, 900, 2700]));

children.push(h2("二、梯度结构"));
children.push(makeTable(["梯度", "志愿数", "位次区间", "分差范围", "录取概率", "策略"], [
  ["冲刺", "6个", "32000-37500", "+1 ~ +6", "20%-40%", "选211/行业强校冲一冲"],
  ["稳妥", "24个", "37500-45000", "-3 ~ -53", "50%-80%", "省内+省外行业强校主力"],
  ["保底", "15个", "45000+", "-26 ~ -70", "90%+", "公办兜底，彻底无滑档"],
], [1000, 1000, 1500, 1500, 1500, 2500]));
children.push(para([{ text: "⚠️ 务必核对2026年各校招生计划：", bold: true, color: "C0392B" }, { text: "专业组可能会拆分/合并，投档分数线会有波动。" }]));
children.push(h2("三、填报前核对三件事（实操话术）"));
children.push(bullet("第一：登录陕西省教育考试院官网，打开2026年《招生计划汇编》，找到目标院校专业组代码，确认该组2026年是否存在、组内包含哪些专业"));
children.push(bullet("第二：检查组内是否有新增的电气工程、自动化、硬件通信等重度物理专业——若有，确认自己是否接受被调剂到这些专业"));
children.push(bullet("第三：核对化工、消防、医学类专业是否新增了色盲/色弱/单色识别限制——若自身条件不符，直接剔除该志愿"));
children.push(new PageBreak());

// ===== 第七部分：赛道就业与薪酬 =====
children.push(h1("第五部分：赛道就业方向与薪酬参考"));
children.push(h2("一、各赛道全貌对比"));
children.push(makeTable(["赛道", "年薪", "典型单位", "成长性", "风险", "负面提示"],
  Object.entries(trackInfo).map(([name, t]) => [name, allVolunteers.find(v => v.track === name)?.salary || "", t.demand, t.growth, "", t.neg]),
  [1800, 1000, 2800, 1500, 0, 1900]
));

children.push(h2("二、地域薪资调整系数"));
children.push(para("本方案薪资以陕西本地为准。若前往其他地区就业，可参考以下系数调整："));
children.push(makeTable(["地域", "薪资上浮", "适用院校"], [
  ["西安本地", "基准（1.0x）", "所有陕西院校"],
  ["长三角（上海/南京/杭州）", "+30%~50%", "南京工业、扬州大学、合肥工业"],
  ["京津冀（北京/天津）", "+40%~60%", "北京信息科技、天津财经、天津理工"],
  ["珠三角（广州/深圳）", "+40%~60%", "（本方案无珠三角院校）"],
  ["西南成渝", "+10%~20%", "西华大学、成都大学、重庆邮电"],
  ["西北能源基地（榆林/鄂尔多斯）", "+15%~25%（补贴）", "榆林学院、延安大学、西安石油"],
], [2000, 2000, 5000]));

children.push(h2("三、学费与生活成本参考"));
children.push(para("以下为公办院校普遍收费标准（陕西省属/市属），供预算参考："));
children.push(makeTable(["专业类别", "学费标准（元/年）", "住宿费（元/年）"], [
  ["化工/环境/材料类", "4500-5200", "800-1200"],
  ["GIS/地理信息类", "4500-5200", "800-1200"],
  ["网安/大数据类", "5200-6480", "800-1200"],
  ["药学/医学类", "5800-7000", "800-1200"],
  ["财经管理类", "4500-5200", "800-1200"],
], [2500, 3000, 3500]));
children.push(para([{ text: "说明：", bold: true }, { text: "211院校学费上浮约10%-20%。省外院校（长三角/北京）住宿费可能略高（1200-1500元/年）。公办院校整体学费在4500-7000元/年区间，家庭负担较轻。" }]));

children.push(h2("四、考研与保研数据参考"));
children.push(makeTable(["院校层次", "保研率", "考研建议", "说明"], [
  ["211院校", "10%-20%", "保研本校/更高层次211", "长安大学、太原理工、合肥工业、东北林大、内大"],
  ["双一流院校", "5%-10%", "考研本校/211", "西南石油、山西大学"],
  ["行业强校", "2%-5%", "考研同赛道211", "西邮、重庆邮电、西安建大"],
  ["普通公办", "1%-3%", "需全力备考", "其余省属/市属院校"],
], [1500, 1500, 2500, 3500]));
children.push(para([{ text: "化工/材料/环境考研价值高：", bold: true }, { text: "硕士进研发岗，薪资比本科提升40%+。网安/大数据本科即可高薪就业，考研边际收益较低。" }]));

children.push(h2("四、选调生硬性门槛"));
children.push(bullet("定向选调：大部分省份要求 双一流+党员+应届生+学生干部经历+（部分省份需校级以上奖励）"));
children.push(bullet("普通选调：公办本科+党员+应届生即可，无211硬性要求，但不限制学校层次的省份较少"));
children.push(bullet("选调生在校时间线：大一提交入党申请书→大二成为积极分子→大三成为预备党员→大四参加选调考试"));
children.push(bullet("本方案中适合走定向选调的院校：东北林业大学(211)、内蒙古大学(211)、山西大学(双一流)"));
children.push(bullet("普通选调本方案中所有公办院校均可报考，竞争更大，需提前备考申论行测"));
children.push(new PageBreak());

// ===== 第八部分：长期规划 =====
children.push(h1("第六部分：长期学业与就业规划"));
children.push(h2("路线一：稳定留陕（推荐保守家庭）"));
children.push(bullet("省内院校前置：西安建大、西安理工、陕科大、西安石油等8所省内稳妥前置"));
children.push(bullet("在校准备：大二考碳排放管理师/测绘证，大四参加陕煤、隆基、省规划院校招"));
children.push(bullet("考研：西安建大、陕科大、长安大学读研，本地研发岗薪资提升40%"));
children.push(para([{ text: "适合人群：", bold: true }, { text: "不想离家太远、追求稳定、能接受化工环境类工作环境的学生" }]));

children.push(h2("路线二：高薪出省"));
children.push(bullet("省外院校前置：重庆邮电、北京信息科技、南京工业、青岛科技"));
children.push(bullet("在校准备：自学Python/数据库/安全运维，大二实习"));
children.push(bullet("目标：长三角新材料、西南数字产业、山东锂电橡胶集群，一线年薪25-35万"));
children.push(para([{ text: "适合人群：", bold: true }, { text: "能接受异地、愿意在民企打拼、追求薪资上限的学生" }]));

children.push(h2("路线三：211考研/选调"));
children.push(bullet("院校重心：冲刺段211 + 东北林大211 + 内大211 + 山西大学双一流"));
children.push(bullet("211学历是省考定向选调硬性门槛"));
children.push(para([{ text: "适合人群：", bold: true }, { text: "想走体制内、愿意在大学努力备考的学生" }]));
children.push(new PageBreak());

// ===== 第九部分：风险防控 =====
children.push(h1("第七部分：风险防控细则（修复版）"));
children.push(h2("一、退档风险"));
children.push(bullet("全部院校勾选服从专业调剂（注意：仅同专业组内生效）"));
children.push(bullet("色盲色弱限制专业：消防、化工、药学、医学、测绘类通常不录取，请逐校核对招生章程"));
children.push(bullet("45个志愿全部填满，不空缺任何位置"));

children.push(h2("二、学习挂科风险"));
children.push(bullet("坚决不报电气工程、自动化、飞行器、硬件通信、纯光电等重度物理专业"));
children.push(bullet("优先化学、地理相关专业，利用高分优势拉高绩点，为保研/考研打基础"));
children.push(bullet("即使在本方案推荐的化工/环境类专业中，也需提前了解有无物理类必修课"));

children.push(h2("三、就业预期风险（客观披露）"));
children.push(h3("化工/能源类"));
children.push(bullet("厂区偏远：长庆油田、电厂、煤化工基地多在远离市区的地方"));
children.push(bullet("倒班制度：四班三倒或三班两倒，需适应夜班"));
children.push(bullet("化学接触：实验室和生产现场需接触有毒有害化学品，需做好防护"));
children.push(h3("铁道/空管类"));
children.push(bullet("轮班制：行车调度、车站值班员需倒班，节假日常在岗"));
children.push(bullet("工作地点：铁路局、地铁公司多在交通枢纽城市，部分岗位在沿线小站"));
children.push(h3("网络安全/大数据类"));
children.push(bullet("加班强度大：项目期996较常见，安全运维需7×24小时待命"));
children.push(bullet("35岁转型压力：纯技术路线有年龄天花板，需提前布局管理/架构方向"));
children.push(bullet("技术迭代快：需持续学习新技术，否则容易被替代"));
children.push(h3("GIS/规划类"));
children.push(bullet("项目期加班：设计院项目期需频繁加班画图"));
children.push(bullet("出差频繁：野外踏勘、项目现场对接，一年有1/3时间在外"));

children.push(h2("四、滑档应急方案"));
children.push(bullet("保底院校二次确认：志愿31-45的15所保底院校2025年位次均在45000+，你的38044位次录取概率90%以上"));
children.push(bullet("若45个志愿全部滑档（概率极低）：关注征集志愿（补录），本科一批征集通常在7月中下旬开始"));
children.push(para([{ text: "征集志愿高频备选院校（陕西本科一批）：", bold: true }, { text: "榆林学院、商洛学院、安康学院、渭南师范学院、西安航空学院、陕西学前师范学院——这些院校在往年征集志愿中经常出现剩余名额，可作为滑档后的第一时间填报目标。" }]));
children.push(bullet("终极兜底：陕西本科二批、高职专科批仍有大量名额，完全有学上"));

children.push(h2("五、部分赛道客观数据补充"));
children.push(h3("铁路系统就业真相"));
children.push(bullet("铁路本科校招正式编制录取比例约20%-30%（非行业子弟）"));
children.push(bullet("非行业子弟提升竞争力的方法：在校期间考取铁路相关证书（如铁路货运员、调度员资格证）、尽量在铁路局实习"));
children.push(bullet("铁路系统优势依然是稳定性第一：五险二金+补充医疗保险+职工住房补贴"));
children.push(h3("化工本科 vs 硕士薪资差距"));
children.push(makeTable(["学历", "陕西综合年薪", "长三角综合年薪", "岗位类型"], [
  ["本科", "13-17万", "17-22万", "工艺操作、质检分析、中控室监控"],
  ["硕士", "19-26万", "25-34万", "研发工程师、工艺设计、研究院"],
  ["博士", "28-40万", "35-55万", "首席工程师、课题组负责人"],
], [1500, 2000, 2000, 3500]));
children.push(para([{ text: "提示：", bold: true }, { text: "化工硕士起薪提升约40%-50%，且硕士学历是进入研发岗的基本门槛。若家庭经济允许，建议本科阶段即做好考研准备。" }]));

// ======== 生成 ========
const doc = new Document({
  styles: {
    default: { document: { run: { font: "宋体", size: 21 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 32, bold: true, font: "微软雅黑", color: "1F4E79" }, paragraph: { spacing: { before: 300, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 28, bold: true, font: "微软雅黑", color: "2E75B6" }, paragraph: { spacing: { before: 240, after: 160 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 24, bold: true, font: "微软雅黑" }, paragraph: { spacing: { before: 200, after: 120 }, outlineLevel: 2 } },
    ],
  },
  numbering: { config: [{ reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] }] },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [new TextRun({ text: "2026陕西高考志愿终极方案（完整修复版）", size: 18, font: "微软雅黑", color: "999999" })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "— ", size: 18, color: "999999" }), new TextRun({ children: [PageNumber.CURRENT], size: 18, color: "999999" }), new TextRun({ text: " —", size: 18, color: "999999" })] })] }) },
    children,
  }],
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync("高考方案_终极版.docx", buffer);
  console.log("✅ 已生成: 高考方案_终极版.docx");
});
