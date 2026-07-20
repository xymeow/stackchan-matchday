---
name: player-catalog-builder
description: 为 stackchan-matchday 扩充全局球员目录（config/espn_player_catalog.json）：拉取 ESPN 球队名单，与现有目录按 athlete id 去重，生成骨架条目，由 agent 提议中文译名、经人工确认后合并。当用户说"建球员目录"、"补英超/某队球员"、"把阿森纳加进目录"、"catalog"、"球员名单"、"球星库"，或解说里球员名字念成了英文原名需要补译名时，使用本 skill。
---

# Player Catalog Builder（球员目录扩充）

目录是解说的"人名数据库"：ESPN athlete id 为主键、别名做保守回退、
中文译名与外号是**编辑内容**。本 skill 自动化"查名单、搭骨架"的体力活，
把需要判断的部分（译名、外号、featured）留给对话确认。

核心原则：**译名确认权在人**。惯用译名（"厄德高"不是"奥德高"）和梗式
外号（"拉师傅"）不能机翻；agent 只提议，用户点头后才写入目录。

## 工作流程

### 1. 确定范围

从用户请求确定联赛与球队。整支球队、整个联赛、或"把 XX 队的首发补齐"
都可以；联赛路径与 market-pairing skill 的 ESPN 表一致（英超
`soccer/eng.1`，欧冠 `soccer/uefa.champions` 等）。

### 2. 拉名单骨架（用脚本，不要手写请求）

```sh
python3 scripts/fetch_roster_candidates.py --espn-league soccer/eng.1 --list-teams
python3 scripts/fetch_roster_candidates.py --espn-league soccer/eng.1 --team Arsenal
```

脚本输出与现有目录**按 espn:id 去重后**的骨架候选（含英文名、别名、
位置、号码上下文），已入库的球员自动跳过。`--team` 接数字 id 或队名
子串；重名歧义时脚本会拒绝并要求用 id。

### 3. 提议中文名

对每个候选给出中文译名提议，按此优先级：

1. 中文足球媒体的**惯用译名**（懂球帝/直播吧口径）；
2. 无惯用译名的年轻球员按新华社译音表规则译；
3. 拿不准的标注"？"单独列出，宁可空着也不要瞎译。

外号（`casual_name`）与进球口号（`goal_chant`）只给用户点名的球星，
不要批量生成；`featured` 只标用户关心的球员。

### 4. 确认与合并

用户确认后合并进 `config/espn_player_catalog.json`：

- 骨架里的 `_context` 字段（球队、位置、号码）是给人看的，**合并时删掉**
  ——目录 schema 不认识它。
- 保留既有条目原样不动；同 id 冲突时以目录现状为准，除非用户明确要改。
- 别名数组保留 ESPN 的 displayName 与 shortName 两个变体；带音符的名字
  （Ødegaard）目录加载器会自动做去音符归一化，不必手工添加变体。
- 合并后跑 `python3 -m unittest discover -s tools -p 'test_stackchan_player_catalog*'`
  确认 schema 合法。

### 5. 汇报

告诉用户：新增了谁（中文名 + 英文名对照表）、跳过了谁（已入库）、
哪些译名待定。解说播报只认目录——没进目录的球员会以 ESPN 英文原名
播出，这是预期的兜底而不是错误。

## 边界

- 只写 `config/espn_player_catalog.json`，不碰 watcher 其他配置。
- 目录 key 现为 `espn:<id>`，**当前仅收录足球球员**。ESPN 不同运动的
  athlete id 未确认全局唯一，跨品类（MLB 等，P4）前需要先决定
  key 方案（如 `espn:soccer:<id>` 或按品类分文件）——遇到非足球请求时
  先提醒这个设计点，不要直接往现有文件里混入其他运动。
- ESPN roster 接口无文档、随时可变；脚本解析失败时把错误如实报出，
  不要猜测数据。
